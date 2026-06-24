"""Episode Planner (Block L.1.3) -- season grid of episodes with status badges.

Drag-and-drop reordering is a Phase-2 enhancement; the MVP lists episodes in
order with their status.
"""
from __future__ import annotations

from nicegui import ui

from ui.components.progress import status_badge
from ui.state import AppState


def render(state: AppState) -> None:
    ui.label("Episode Planner").classes("text-2xl font-bold")

    episodes = []
    try:
        from modules.db import EpisodeRow, get_session

        with get_session() as s:
            episodes = s.query(EpisodeRow).order_by(EpisodeRow.created_at).all() \
                if hasattr(s, "query") else []
    except Exception:
        pass

    if not episodes:
        ui.label("Пока нет эпизодов. Создайте первый в Studio.").classes("text-grey")
        ui.link("Открыть Studio →", "/studio")
        return

    with ui.card().classes("w-full"):
        for ep in episodes:
            with ui.row().classes("items-center w-full justify-between"):
                ui.label(f"{ep.slug} — {ep.thesis[:60]}")
                status_badge(ep.status)
    ui.label("Drag-and-drop переупорядочивание — Фаза 2 (см. ТЗ Раздел 8).").classes("text-xs text-grey q-mt-md")
