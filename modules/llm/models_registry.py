"""Maps api_format -> adapter class and builds a configured adapter."""
from __future__ import annotations

from modules.llm.adapters.anthropic_adapter import AnthropicAdapter
from modules.llm.adapters.base import ModelAdapter
from modules.llm.adapters.falcon_adapter import FalconAdapter
from modules.llm.adapters.gigachat_adapter import GigaChatAdapter
from modules.llm.adapters.google_adapter import GoogleAdapter
from modules.llm.adapters.hyperclova_adapter import HyperClovaAdapter
from modules.llm.adapters.mock_adapter import MockAdapter
from modules.llm.adapters.openai_adapter import OpenAIAdapter
from modules.llm.adapters.yandex_adapter import YandexAdapter

ADAPTER_BY_FORMAT: dict[str, type[ModelAdapter]] = {
    "openai": OpenAIAdapter,
    "anthropic": AnthropicAdapter,
    "google": GoogleAdapter,
    "yandex": YandexAdapter,
    "gigachat": GigaChatAdapter,
    "falcon": FalconAdapter,
    "hyperclova": HyperClovaAdapter,
    "mock": MockAdapter,
}


def build_adapter(
    model_cfg: dict, api_key: str | None, timeout: int, *, offline: bool = False
) -> ModelAdapter:
    fmt = "mock" if offline else model_cfg["api_format"]
    cls = ADAPTER_BY_FORMAT.get(fmt)
    if cls is None:
        raise ValueError(f"No adapter registered for api_format '{fmt}'")
    return cls(model_cfg, api_key, timeout)
