"""Dashboard (Module 13 / Block L.1.1)."""
from __future__ import annotations

from nicegui import ui

from ui.state import AppState


def render(state: AppState) -> None:
    cfg = state.config
    ui.label("Dashboard").classes("text-2xl font-bold")

    with ui.row().classes("w-full q-gutter-md"):
        with ui.card().classes("col"):
            ui.label("Текущий эпизод").classes("font-bold")
            if state.last_episode is not None:
                ep = state.last_episode
                ui.label(f"{ep.slug} — {ep.status.value}")
                ui.label(ep.thesis).classes("text-sm text-grey")
            else:
                ui.label("Нет активного эпизода. Перейдите в Studio.").classes("text-grey")
                ui.link("Открыть Studio →", "/studio")

        with ui.card().classes("col"):
            ui.label("Модели в реестре").classes("font-bold")
            s1 = len(cfg.models_by_season(1))
            s2 = len(cfg.models_by_season(2))
            ui.label(f"Сезон 1: {s1} · Сезон 2: {s2}")
            sanc = [m["display_name"] for m in cfg.models if m.get("sanctions_risk")]
            if sanc:
                ui.label(f"⚠ Санкционный риск: {', '.join(sanc)}").classes("text-red text-sm")

    with ui.card().classes("w-full q-mt-md"):
        ui.label("Лидерборд (top-5)").classes("font-bold")
        _render_leaderboard_top5(state)


def _render_leaderboard_top5(state: AppState) -> None:
    try:
        from modules.db import LeaderboardRow, get_session

        with get_session() as s:
            rows = s.query(LeaderboardRow).order_by(LeaderboardRow.win_count.desc()).limit(5).all() \
                if hasattr(s, "query") else []
        if not rows:
            ui.label("Пока нет данных лидерборда (введите Oxford-дельту после публикации).").classes("text-grey")
            return
        columns = [
            {"name": "model", "label": "Model", "field": "model"},
            {"name": "wins", "label": "Wins", "field": "wins"},
            {"name": "streak", "label": "Streak", "field": "streak"},
        ]
        data = [{"model": r.model_id, "wins": r.win_count, "streak": r.win_streak} for r in rows]
        ui.table(columns=columns, rows=data).classes("w-full")
    except Exception as exc:
        ui.label(f"Лидерборд недоступен: {exc}").classes("text-grey")
