"""OpenAI-compatible adapter.

Drives every model whose api_format is "openai" by swapping base_url: OpenAI,
DeepSeek, Qwen/DashScope, Mistral, xAI/Grok, Zhipu GLM, AI21 Jamba, Sarvam, and
Rakuten via OpenRouter. Reads reasoning_content for reasoning models (DeepSeek).
"""
from __future__ import annotations

import time

from modules.llm.adapters.base import AdapterError, ModelAdapter
from modules.schemas import GenerationResult


class OpenAIAdapter(ModelAdapter):
    api_format = "openai"

    def generate(self, system, user, temperature, max_tokens, seed=None):
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise AdapterError("openai SDK not installed") from exc

        client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                seed=seed,
            )
        except Exception as exc:  # network/auth/rate -> let orchestrator retry policy decide
            raise AdapterError(f"{type(exc).__name__}: {exc}") from exc

        choice = resp.choices[0]
        msg = choice.message
        reasoning = getattr(msg, "reasoning_content", None)  # DeepSeek V4 CoT field
        usage = None
        if getattr(resp, "usage", None) is not None:
            try:
                usage = resp.usage.model_dump()
            except Exception:
                usage = dict(getattr(resp.usage, "__dict__", {}))

        return GenerationResult(
            content=msg.content or "",
            reasoning_content=reasoning,
            finish_reason=choice.finish_reason or "stop",
            model_id=self.model_id,
            model_version=getattr(resp, "model", None),
            latency_ms=int((time.time() - t0) * 1000),
            prompt_hash=self._hash(system, user),
            usage=usage,
        )
