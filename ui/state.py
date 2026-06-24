"""Shared UI state (single-operator, single-process)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from modules.config import Config


@dataclass
class AppState:
    config: Config | None = None
    offline_default: bool = True
    last_episode: Any = None          # modules.schemas.Episode
    last_result: dict | None = None   # run_full_pipeline result paths
    extras: dict = field(default_factory=dict)
