"""Google Gemini adapter (Gemini 3.1 Pro), unified google-genai SDK.

Also used by the translation corrector (grammar-only Gemini call).
"""
from __future__ import annotations

import time

from modules.llm.adapters.base import AdapterError, ModelAdapter
from modules.schemas import GenerationResult


class GoogleAdapter(ModelAdapter):
    api_format = "google"

    def generate(self, system, user, temperature, max_tokens, seed=None):
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise AdapterError("google-genai SDK not installed") from exc

        client = genai.Client(api_key=self.api_key)
        t0 = time.time()
        try:
            resp = client.models.generate_content(
                model=self.model_name,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                    seed=seed,
                ),
            )
        except Exception as exc:
            raise AdapterError(f"{type(exc).__name__}: {exc}") from exc

        finish = "stop"
        try:
            finish = str(resp.candidates[0].finish_reason)
        except Exception:
            pass
        return GenerationResult(
            content=getattr(resp, "text", "") or "",
            finish_reason=finish,
            model_id=self.model_id,
            model_version=self.model_name,
            latency_ms=int((time.time() - t0) * 1000),
            prompt_hash=self._hash(system, user),
        )
