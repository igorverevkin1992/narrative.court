"""Falcon-H1 adapter (TII via HuggingFace Inference API, text-generation)."""
from __future__ import annotations

import time

from modules.llm.adapters.base import AdapterError, ModelAdapter, classify_adapter_error
from modules.schemas import GenerationResult


class FalconAdapter(ModelAdapter):
    api_format = "falcon"

    def generate(self, system, user, temperature, max_tokens, seed=None):
        try:
            import httpx  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise AdapterError("httpx not installed") from exc

        url = f"{self.base_url.rstrip('/')}/models/{self.model_name}"
        prompt = f"<|system|>\n{system}\n<|user|>\n{user}\n<|assistant|>\n"
        body = {
            "inputs": prompt,
            "parameters": {
                "temperature": max(temperature, 0.01),
                "max_new_tokens": max_tokens,
                "return_full_text": False,
            },
        }
        t0 = time.time()
        try:
            resp = httpx.post(
                url, headers={"Authorization": f"Bearer {self.api_key}"},
                json=body, timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise classify_adapter_error(exc) from exc

        if isinstance(data, list) and data:
            text = data[0].get("generated_text", "")
        else:
            text = data.get("generated_text", "") if isinstance(data, dict) else ""
        return GenerationResult(
            content=text,
            finish_reason="stop",
            model_id=self.model_id,
            model_version=self.model_name,
            latency_ms=int((time.time() - t0) * 1000),
            prompt_hash=self._hash(system, user),
        )
