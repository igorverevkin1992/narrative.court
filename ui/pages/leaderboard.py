"""Leaderboard (Block L.1.6) -- 5 metrics + Oxford delta entry."""
from __future__ import annotations

from nicegui import ui

from ui.state import AppState


def render(state: AppState) -> None:
    ui.label("Leaderboard").classes("text-2xl font-bold")

    columns = [
        {"name": "model", "label": "Model", "field": "model"},
        {"name": "wins", "label": "Wins", "field": "wins"},
        {"name": "obj", "label": "Obj%", "field": "obj"},
        {"name": "ref", "label": "Ref%", "field": "ref"},
        {"name": "avg", "label": "Avg Sus", "field": "avg"},
        {"name": "streak", "label": "Streak", "field": "streak"},
    ]
    rows = []
    try:
        from modules.db import LeaderboardRow, get_session

        with get_session() as s:
            db_rows = s.query(LeaderboardRow).all() if hasattr(s, "query") else []
        for r in db_rows:
            rows.append({
                "model": r.model_id, "wins": r.win_count,
                "obj": "n/a" if r.objection_sustained_pct is None else f"{r.objection_sustained_pct:.0f}%",
                "ref": "n/a" if r.explicit_refusal_pct is None else f"{r.explicit_refusal_pct:.0f}%",
                "avg": f"{r.avg_sustained_per_episode:.1f}", "streak": r.win_streak,
            })
    except Exception:
        pass
    ui.table(columns=columns, rows=rows).classes("w-full")
    if not rows:
        ui.label("Лидерборд пуст. Введите Oxford-дельту опубликованного эпизода ниже.").classes("text-grey")

    with ui.card().classes("w-full q-mt-md"):
        ui.label("Ввод Oxford-дельты (после публикации)").classes("font-bold")
        with ui.row():
            ab = ui.number("agree before %", value=0, min=0, max=100).classes("w-40")
            aa = ui.number("agree after %", value=0, min=0, max=100).classes("w-40")
            vb = ui.number("votes before", value=0, min=0).classes("w-40")
        with ui.row():
            db_ = ui.number("disagree before %", value=0, min=0, max=100).classes("w-40")
            da = ui.number("disagree after %", value=0, min=0, max=100).classes("w-40")
            va = ui.number("votes after", value=0, min=0).classes("w-40")
        out = ui.label("").classes("text-blue")

        def preview():
            from uuid import uuid4

            from modules.schemas import OxfordDelta
            d = OxfordDelta(
                episode_id=uuid4(), agree_before=ab.value, agree_after=aa.value,
                disagree_before=db_.value, disagree_after=da.value,
                votes_before=int(vb.value), votes_after=int(va.value),
            )
            if d.no_quorum:
                out.set_text("no_quorum (< 30 голосов) — победа не засчитывается")
            else:
                out.set_text(
                    f"winner side: {d.winner_side.value} "
                    f"(Δpros={d.delta_prosecution:+.0f}, Δdef={d.delta_defense:+.0f})"
                )
        ui.button("Предпросмотр результата", on_click=preview).props("color=primary")
        ui.label("Полный пересчёт и экспорт MD/CSV — модуль leaderboard.engine/export.").classes("text-xs text-grey")
