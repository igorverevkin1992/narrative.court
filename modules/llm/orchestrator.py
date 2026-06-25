"""LLM Orchestrator (Module 1).

Single entry point to all 15 models: adapter selection, secret resolution, retry
with exponential backoff + jitter (tenacity), GenerationLog persistence (JSONL,
all runs), and an offline switch that routes every model through MockAdapter.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import UUID

import tenacity

from modules.config import Config, get_secret
from modules.llm.adapters.base import AdapterError, AuthError, SanctionsBlockedError
from modules.llm.models_registry import build_adapter
from modules.schemas import GenerationLog, GenerationResult


def _retryable(exc: BaseException) -> bool:
    """Retry transient adapter errors, but never sanctions blocks or client
    errors (auth/permission/bad-request) that retrying cannot fix (Block B.2)."""
    if isinstance(exc, (SanctionsBlockedError, AuthError)):
        return False
    return isinstance(exc, AdapterError)


class Orchestrator:
    def __init__(self, config: Config, *, offline: bool = False, logs_dir: str | Path | None = None):
        self.config = config
        self.offline = offline
        self.logs_dir = Path(logs_dir) if logs_dir else config.resolve_path("logs_dir")
        # Cap concurrent live API calls so parallel rounds/quickfire do not blow
        # provider RPM/TPM limits (Block B.2). Shared across this run's calls.
        max_parallel = int(config.generation_defaults.get("max_parallel_requests", 4))
        self._sem = asyncio.Semaphore(max(1, max_parallel))

    def _timeout(self, model_cfg: dict) -> int:
        gd = self.config.generation_defaults
        if model_cfg.get("behaviour_profile", {}).get("reasoning"):
            return int(gd.get("reasoning_timeout_seconds", 180))
        return int(gd.get("timeout_seconds", 120))

    def generate(
        self,
        model_id: str,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
        seed: int | None = None,
        *,
        episode_id: UUID | None = None,
        round_id: str = "",
    ) -> GenerationResult:
        model_cfg = self.config.model(model_id)
        if model_cfg is None:
            raise ValueError(f"Unknown model_id '{model_id}'")

        api_key = None if self.offline else get_secret(model_cfg["api_key_env"])
        if not self.offline and not api_key:
            raise AdapterError(
                f"Missing API key {model_cfg['api_key_env']} for {model_id}"
            )

        adapter = build_adapter(
            model_cfg, api_key, self._timeout(model_cfg), offline=self.offline
        )
        attempts = int(self.config.generation_defaults.get("retry_attempts", 3))
        retryer = tenacity.Retrying(
            stop=tenacity.stop_after_attempt(attempts),
            wait=tenacity.wait_exponential(multiplier=2, min=2, max=30)
            + tenacity.wait_random(0, 1),
            retry=tenacity.retry_if_exception(_retryable),
            reraise=True,
        )

        attempt_no = 1
        result: GenerationResult | None = None
        for attempt in retryer:
            with attempt:
                attempt_no = attempt.retry_state.attempt_number
                result = adapter.generate(system, user, temperature, max_tokens, seed)

        assert result is not None
        self._log(episode_id, model_id, round_id, attempt_no, system, user,
                  temperature, seed, result)
        return result

    async def agenerate(self, *args, **kwargs) -> GenerationResult:
        """Async wrapper: runs the sync adapter call in a worker thread under a
        concurrency semaphore (Block B.2 rate-limit safety)."""
        async with self._sem:
            return await asyncio.to_thread(self.generate, *args, **kwargs)

    def _log(self, episode_id, model_id, round_id, attempt_no, system, user,
             temperature, seed, result) -> None:
        ep = str(episode_id) if episode_id else "adhoc"
        log = GenerationLog(
            episode_id=episode_id or UUID(int=0),
            model_id=model_id, round_id=round_id, attempt_number=attempt_no,
            system_prompt=system, user_prompt=user, temperature=temperature,
            seed=seed, result=result, selected=True, selection_policy="first_valid",
        )
        target = self.logs_dir / ep
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{model_id}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(log.model_dump_json() + "\n")
        # Retention: cap each model log to the last N runs (Block B.3).
        retention = int(self.config.get("logging", "retention_runs", default=200))
        if retention > 0:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
                if len(lines) > retention:
                    path.write_text("\n".join(lines[-retention:]) + "\n", encoding="utf-8")
            except Exception:
                pass
