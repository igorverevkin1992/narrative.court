"""Dashboard (Module 13 / Block L.1.1)."""
from __future__ import annotations

from nicegui import ui

from modules.config import is_first_run, missing_keys
from modules.leaderboard.service import episodes_pending_delta
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

    # --- O.7 readiness: missing API keys / first run ------------------------
    try:
        miss = missing_keys(cfg)
    except Exception:
        miss = []
    if is_first_run() or miss:
        with ui.card().classes("w-full q-mt-md"):
            ui.label("⚙ Готовность к работе").classes("font-bold")
            if is_first_run():
                ui.label("Первый запуск: БД создана, реестр моделей засеян.").classes("text-sm")
            if miss:
                shown = ", ".join(miss[:6]) + (" …" if len(miss) > 6 else "")
                ui.label(f"Не заданы ключи ({len(miss)}): {shown}").classes("text-sm text-red")
                ui.label("В offline-режиме ключи не нужны; для live-генерации задайте их.")\
                    .classes("text-xs text-grey")
                ui.link("Настроить ключи → Config", "/config")
            else:
                ui.label("Все ключи заданы ✓").classes("text-sm text-green-700")

    # --- O.5 episodes awaiting an Oxford delta ------------------------------
    try:
        pending = episodes_pending_delta(cfg)
    except Exception:
        pending = []
    if pending:
        with ui.card().classes("w-full q-mt-md"):
            ui.label("⏳ Ожидают Oxford-дельту").classes("font-bold")
            ui.label(f"{len(pending)} эпизод(ов) экспортировано без ввода результатов опроса "
                     "(лидерборд не закрыт):").classes("text-sm")
            for ep in pending[:5]:
                ui.label(f"• {ep.slug} — {ep.thesis[:50]}").classes("text-xs text-grey")
            ui.link("Ввести дельту → Leaderboard", "/leaderboard")

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
