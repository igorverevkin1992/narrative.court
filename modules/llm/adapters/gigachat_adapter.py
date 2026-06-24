"""GigaChat adapter (Sber) -- OAuth token exchange + OpenAI-like chat.

[SANCTIONS RISK - legal review required, INA §329]. Sberbank is under OFAC SDN
blocking sanctions (2022-04-06). HTTP 403 / auth denial is surfaced as a
SanctionsBlockedError so the UI can show the legal warning (Block O.6).

Note: GigaChat uses a Russian Ministry CA. A real deployment must point httpx at
that CA bundle (verify=<path>); do NOT disable TLS verification.
"""
from __future__ import annotations

import os
import time

from modules.llm.adapters.base import AdapterError, ModelAdapter, SanctionsBlockedError
from modules.schemas import GenerationResult

_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"


class GigaChatAdapter(ModelAdapter):
    api_format = "gigachat"

    def _get_token(self, httpx) -> str:
        scope = os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
        headers = {
            "Authorization": f"Basic {self.api_key}",
            "Content-Type": "application/x-www-form-urlencoded",
            "RqUID": "00000000-0000-0000-0000-000000000001",
        }
        verify = os.environ.get("GIGACHAT_CA_BUNDLE", True)
        resp = httpx.post(_OAUTH_URL, headers=headers, data={"scope": scope},
                          timeout=self.timeout, verify=verify)
        if resp.status_code == 403:
            raise SanctionsBlockedError("GigaChat OAuth returned 403 (sanctions/region block)")
        resp.raise_for_status()
        return resp.json()["access_token"]

    def generate(self, system, user, temperature, max_tokens, seed=None):
        try:
            import httpx  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise AdapterError("httpx not installed") from exc

        t0 = time.time()
        verify = os.environ.get("GIGACHAT_CA_BUNDLE", True)
        try:
            token = self._get_token(httpx)
            url = f"{self.base_url.rstrip('/')}/chat/completions"
            resp = httpx.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "model": self.model_name,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=self.timeout,
                verify=verify,
            )
            if resp.status_code == 403:
                raise SanctionsBlockedError("GigaChat chat returned 403 (sanctions/region block)")
            resp.raise_for_status()
            data = resp.json()
        except SanctionsBlockedError:
            raise
        except Exception as exc:
            raise AdapterError(f"{type(exc).__name__}: {exc}") from exc

        choice = data["choices"][0]
        return GenerationResult(
            content=choice["message"]["content"],
            finish_reason=choice.get("finish_reason", "stop"),
            model_id=self.model_id,
            model_version=data.get("model"),
            latency_ms=int((time.time() - t0) * 1000),
            prompt_hash=self._hash(system, user),
            usage=data.get("usage"),
        )
