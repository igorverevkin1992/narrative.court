"""Config & Security (Module 14).

Loads config/config.yaml and resolves secrets with the policy: OS keyring first,
then config/.env (python-dotenv). Heavy/optional deps (keyring, dotenv) are
imported lazily so the rest of the system runs without them.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.yaml"
ENV_PATH = CONFIG_DIR / ".env"
KEYRING_SERVICE = "narrative_court"

_DOTENV_LOADED = False


class Config:
    """Thin typed accessor over the parsed config.yaml dict."""

    def __init__(self, data: dict, path: Path):
        self._data = data
        self.path = path

    # -- generic access -----------------------------------------------------
    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self._data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    # -- typed shortcuts ----------------------------------------------------
    @property
    def app(self) -> dict:
        return self._data.get("app", {})

    @property
    def generation_defaults(self) -> dict:
        return self._data.get("generation_defaults", {})

    @property
    def tts_defaults(self) -> dict:
        return self._data.get("tts_defaults", {})

    @property
    def models(self) -> list[dict]:
        return self._data.get("models", [])

    def model(self, model_id: str) -> dict | None:
        return next((m for m in self.models if m["id"] == model_id), None)

    def models_by_season(self, season: int) -> list[dict]:
        return [m for m in self.models if m.get("season") == season]

    def resolve_path(self, key: str) -> Path:
        """Resolve an app path (relative to repo root)."""
        raw = self.app.get(key, "")
        p = Path(raw)
        return p if p.is_absolute() else (ROOT / p).resolve()


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"config.yaml not found at {path}. Copy config/config.yaml or run onboarding."
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # explicit, with line info
        raise ValueError(f"config.yaml is not valid YAML: {exc}") from exc
    return Config(data, path)


def _load_dotenv_once() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    if not ENV_PATH.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(ENV_PATH)
    except Exception:
        # Minimal manual parser fallback if python-dotenv is absent.
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


def get_secret(env_name: str) -> str | None:
    """Resolve a secret: keyring first, then config/.env / process env."""
    try:
        import keyring  # type: ignore

        value = keyring.get_password(KEYRING_SERVICE, env_name)
        if value:
            return value
    except Exception:
        pass
    _load_dotenv_once()
    value = os.environ.get(env_name)
    return value or None


def set_secret(env_name: str, value: str) -> bool:
    """Store a secret in the OS keyring (used by the onboarding wizard)."""
    try:
        import keyring  # type: ignore

        keyring.set_password(KEYRING_SERVICE, env_name, value)
        return True
    except Exception:
        return False


def mask(value: str | None) -> str:
    """Mask a secret for display in the UI/logs."""
    if not value:
        return "(not set)"
    if len(value) <= 6:
        return "*" * len(value)
    return value[:3] + "*" * (len(value) - 6) + value[-3:]


def is_first_run(db_path: Path | None = None) -> bool:
    """Onboarding trigger (Block O.7): no DB file yet."""
    if db_path is None:
        try:
            db_path = load_config().resolve_path("db_path")
        except Exception:
            return True
    return not Path(db_path).exists()
