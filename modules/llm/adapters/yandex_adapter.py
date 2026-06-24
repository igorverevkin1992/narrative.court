"""YandexGPT adapter (Yandex Cloud foundationModels completion API)."""
from __future__ import annotations

import os
import time

from modules.llm.adapters.base import AdapterError, ModelAdapter, classify_adapter_error
from modules.schemas import GenerationResult


class YandexAdapter(ModelAdapter):
    api_format = "yandex"

    def generate(self, system, user, temperature, max_tokens, seed=None):
        try:
            import httpx  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise AdapterError("httpx not installed") from exc

        folder_id = os.environ.get("YANDEX_FOLDER_ID", "")
        url = f"{self.base_url.rstrip('/')}/completion"
        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "x-folder-id": folder_id,
            "Content-Type": "application/json",
        }
        body = {
            "modelUri": f"gpt://{folder_id}/{self.model_name}",
            "completionOptions": {"temperature": temperature, "maxTokens": str(max_tokens)},
            "messages": [
                {"role": "system", "text": system},
                {"role": "user", "text": user},
            ],
        }
        t0 = time.time()
        try:
            resp = httpx.post(url, headers=headers, json=body, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise classify_adapter_error(exc) from exc

        text = data["result"]["alternatives"][0]["message"]["text"]
        return GenerationResult(
            content=text,
            finish_reason=data["result"]["alternatives"][0].get("status", "stop"),
            model_id=self.model_id,
            model_version=data["result"].get("modelVersion"),
            latency_ms=int((time.time() - t0) * 1000),
            prompt_hash=self._hash(system, user),
        )
