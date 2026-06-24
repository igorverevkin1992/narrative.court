"""ModelAdapter ABC (Block B.1) -- one interface for all 15 models."""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod

from modules.schemas import GenerationResult


def prompt_hash(system: str, user: str) -> str:
    digest = hashlib.sha256((system + "\x00" + user).encode("utf-8")).hexdigest()
    return "sha256:" + digest[:16]


class AdapterError(RuntimeError):
    """Generic, retryable adapter failure (network/5xx/rate-limit/parse)."""


class AuthError(AdapterError):
    """Non-retryable client error (auth/permission/bad request: 400/401/403)."""


class SanctionsBlockedError(AdapterError):
    """Raised when a provider denies access for sanctions/region reasons (Block O.6)."""


# HTTP status codes that indicate a client-side problem retrying will not fix.
_NON_RETRYABLE_STATUS = {400, 401, 403, 404, 422}


def classify_adapter_error(exc: Exception) -> AdapterError:
    """Map a provider/SDK/httpx exception to AuthError (non-retryable) or AdapterError.

    Looks for a status code on the exception itself, its ``response``, or a
    ``code`` attribute (covers openai/anthropic SDK errors and httpx
    HTTPStatusError). Anything else is treated as transient and retryable.
    """
    status = (
        getattr(exc, "status_code", None)
        or getattr(getattr(exc, "response", None), "status_code", None)
        or getattr(exc, "code", None)
    )
    msg = f"{type(exc).__name__}: {exc}"
    if isinstance(status, int) and status in _NON_RETRYABLE_STATUS:
        return AuthError(msg)
    return AdapterError(msg)


class ModelAdapter(ABC):
    """Base class. Concrete adapters are constructed per model with its config."""

    api_format: str = "base"

    def __init__(self, model_cfg: dict, api_key: str | None, timeout: int = 120):
        self.cfg = model_cfg
        self.model_id: str = model_cfg["id"]
        self.model_name: str = model_cfg["model_name"]
        self.base_url: str | None = model_cfg.get("base_url")
        self.api_key = api_key
        self.timeout = timeout

    @abstractmethod
    def generate(
        self,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
        seed: int | None = None,
    ) -> GenerationResult:
        """Return a normalized GenerationResult. Raise AdapterError on failure."""

    # convenience for subclasses
    def _hash(self, system: str, user: str) -> str:
        return prompt_hash(system, user)
