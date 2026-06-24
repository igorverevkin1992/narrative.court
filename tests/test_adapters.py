"""Adapter-layer tests: build_adapter routing, offline->mock, error
classification (auth vs transient), retry policy, and the GigaChat sanctions
(403 -> SanctionsBlockedError) path via a mocked httpx."""
from __future__ import annotations

import sys
import types

import pytest

from modules.llm.adapters.base import (
    AdapterError,
    AuthError,
    SanctionsBlockedError,
    classify_adapter_error,
)
from modules.llm.adapters.mock_adapter import MockAdapter
from modules.llm.models_registry import build_adapter
from modules.llm.orchestrator import _retryable


def _cfg(fmt: str = "openai", **kw) -> dict:
    base = {"id": "gpt-5.5", "model_name": "gpt-5.5", "api_format": fmt, "base_url": None}
    base.update(kw)
    return base


def test_build_adapter_offline_routes_to_mock():
    assert isinstance(build_adapter(_cfg("openai"), None, 120, offline=True), MockAdapter)


def test_build_adapter_maps_formats():
    from modules.llm.adapters.gigachat_adapter import GigaChatAdapter
    from modules.llm.adapters.openai_adapter import OpenAIAdapter

    assert isinstance(build_adapter(_cfg("openai"), "k", 120), OpenAIAdapter)
    assert isinstance(build_adapter(_cfg("gigachat", base_url="https://x"), "k", 120), GigaChatAdapter)


def test_build_adapter_unknown_format_raises():
    with pytest.raises(ValueError):
        build_adapter(_cfg("nope"), "k", 120)


def test_classify_auth_vs_transient():
    class E(Exception):
        def __init__(self, sc):
            self.status_code = sc
            super().__init__("x")

    for sc in (400, 401, 403, 404, 422):
        assert isinstance(classify_adapter_error(E(sc)), AuthError)
    for sc in (429, 500, 503):
        err = classify_adapter_error(E(sc))
        assert isinstance(err, AdapterError) and not isinstance(err, AuthError)
    assert not isinstance(classify_adapter_error(RuntimeError("net")), AuthError)


def test_retryable_excludes_auth_and_sanctions():
    assert _retryable(AdapterError("x")) is True
    assert _retryable(AuthError("x")) is False
    assert _retryable(SanctionsBlockedError("x")) is False
    assert _retryable(ValueError("x")) is False


def test_gigachat_403_raises_sanctions(monkeypatch):
    class Resp:
        status_code = 403

        def json(self):
            return {}

        def raise_for_status(self):
            raise AssertionError("should not be reached on 403")

    fake_httpx = types.SimpleNamespace(post=lambda *a, **k: Resp())
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    from modules.llm.adapters.gigachat_adapter import GigaChatAdapter

    ad = GigaChatAdapter(
        _cfg("gigachat", id="gigachat-ultra", model_name="GigaChat-Ultra", base_url="https://x"),
        "key", 30,
    )
    with pytest.raises(SanctionsBlockedError):
        ad.generate("s", "u", 0.7, 100)
