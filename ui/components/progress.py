"""Small reusable UI helpers."""
from __future__ import annotations

from nicegui import ui


def status_badge(status: str):
    """Colored chip for an episode/topic status."""
    colors = {
        "draft": "grey", "smoke_tested": "blue", "generated": "teal",
        "tts_done": "cyan", "exported": "green", "published": "purple",
        "approved": "green", "warning": "orange", "rejected": "red",
        "no_quorum": "orange", "pending_delta": "orange",
    }
    return ui.badge(status, color=colors.get(status, "grey"))
