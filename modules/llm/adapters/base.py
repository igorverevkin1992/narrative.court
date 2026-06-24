"""ModelAdapter ABC (Block B.1) -- one interface for all 15 models."""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod

from modules.schemas import GenerationResult


def prompt_hash(system: str, user: str) -> str:
    digest = hashlib.sha256((system + "\x00" + user).encode("utf-8")).hexdigest()
    return "sha256:" + digest[:16]


class AdapterError(RuntimeError):
    """Generic adapter failure (network/auth/parse)."""


class SanctionsBlockedError(AdapterError):
    """Raised when a provider denies access for sanctions/region reasons (Block O.6)."""


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
