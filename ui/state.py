"""Shared UI state (single-operator, single-process)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from modules.config import Config


@dataclass
class AppState:
    # NOTE: this is a single process-wide instance shared by every browser
    # connection. The Narrative Court is a single-operator local tool, so drive
    # it from ONE browser tab at a time -- a second tab shares the same in-flight
    # studio episode (extras["studio"]) and will collide. For multi-operator use,
    # move per-session fields into nicegui app.storage.tab.
    config: Config | None = None
    offline_default: bool = True
    last_episode: Any = None          # modules.schemas.Episode
    last_result: dict | None = None   # run_full_pipeline result paths
    extras: dict = field(default_factory=dict)
