"""Anthropic adapter (Claude Opus 4.8). System prompt is a top-level parameter."""
from __future__ import annotations

import time

from modules.llm.adapters.base import AdapterError, ModelAdapter, classify_adapter_error
from modules.schemas import GenerationResult


class AnthropicAdapter(ModelAdapter):
    api_format = "anthropic"

    def generate(self, system, user, temperature, max_tokens, seed=None):
        try:
            from anthropic import Anthropic  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise AdapterError("anthropic SDK not installed") from exc

        # max_retries=0: the orchestrator's tenacity is the single retry authority
        # (avoids SDK retries stacking with ours into long hangs).
        client = Anthropic(api_key=self.api_key, timeout=self.timeout, max_retries=0)
        t0 = time.time()
        try:
            resp = client.messages.create(
                model=self.model_name,
                system=system,
                max_tokens=max_tokens,
                temperature=min(float(temperature), 1.0),  # Anthropic caps temperature at 1.0
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:
            raise classify_adapter_error(exc) from exc

        text = "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text"
        )
        usage = None
        if getattr(resp, "usage", None) is not None:
            usage = {
                "input_tokens": getattr(resp.usage, "input_tokens", None),
                "output_tokens": getattr(resp.usage, "output_tokens", None),
            }
        return GenerationResult(
            content=text,
            finish_reason=getattr(resp, "stop_reason", None) or "stop",
            model_id=self.model_id,
            model_version=getattr(resp, "model", None),
            latency_ms=int((time.time() - t0) * 1000),
            prompt_hash=self._hash(system, user),
            usage=usage,
        )
