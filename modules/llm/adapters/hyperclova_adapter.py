"""HyperCLOVA X adapter (Naver Clova Studio chat-completions)."""
from __future__ import annotations

import os
import time

from modules.llm.adapters.base import AdapterError, ModelAdapter, classify_adapter_error
from modules.schemas import GenerationResult


class HyperClovaAdapter(ModelAdapter):
    api_format = "hyperclova"

    def generate(self, system, user, temperature, max_tokens, seed=None):
        try:
            import httpx  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise AdapterError("httpx not installed") from exc

        url = f"{self.base_url.rstrip('/')}/testapp/v1/chat-completions/{self.model_name}"
        headers = {
            "X-NCP-CLOVASTUDIO-API-KEY": self.api_key or "",
            "X-NCP-APIGW-API-KEY": os.environ.get("HYPERCLOVA_APIGW_KEY", ""),
            "Content-Type": "application/json",
        }
        body = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "maxTokens": max_tokens,
        }
        t0 = time.time()
        try:
            resp = httpx.post(url, headers=headers, json=body, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise classify_adapter_error(exc) from exc

        text = data.get("result", {}).get("message", {}).get("content", "")
        return GenerationResult(
            content=text,
            finish_reason=data.get("result", {}).get("stopReason", "stop"),
            model_id=self.model_id,
            model_version=self.model_name,
            latency_ms=int((time.time() - t0) * 1000),
            prompt_hash=self._hash(system, user),
        )
