"""Leaderboard (Block L.1.6 / I) -- 5 metrics per model, Oxford-delta entry,
full recompute, and Markdown/CSV export."""
from __future__ import annotations

from pathlib import Path

from nicegui import ui

from modules.episodes.manager import list_saved_episodes, save_episode
from modules.leaderboard import service
from modules.schemas import EpisodeStatus, OxfordDelta
from ui.state import AppState

_COLUMNS = [
    {"name": "model", "label": "Model", "field": "model", "align": "left"},
    {"name": "wins", "label": "Wins", "field": "wins"},
    {"name": "obj", "label": "Obj%", "field": "obj"},
    {"name": "ref", "label": "Ref%", "field": "ref"},
    {"name": "avg", "label": "Avg Sus", "field": "avg"},
    {"name": "streak", "label": "Streak", "field": "streak"},
]


def render(state: AppState) -> None:
    cfg = state.config
    ui.label("Leaderboard").classes("text-2xl font-bold")

    @ui.refreshable
    def table() -> None:
        rows = []
        try:
            from modules.db import LeaderboardRow, get_session

            with get_session() as s:
                db_rows = s.query(LeaderboardRow).order_by(
                    LeaderboardRow.win_count.desc()).all() if hasattr(s, "query") else []
            for r in db_rows:
                rows.append({
                    "model": r.model_id, "wins": r.win_count,
                    "obj": "n/a" if r.objection_sustained_pct is None else f"{r.objection_sustained_pct:.0f}%",
                    "ref": "n/a" if r.explicit_refusal_pct is None else f"{r.explicit_refusal_pct:.0f}%",
                    "avg": f"{r.avg_sustained_per_episode:.1f}", "streak": r.win_streak,
                })
        except Exception:
            pass
        ui.table(columns=_COLUMNS, rows=rows, row_key="model").classes("w-full")
        if not rows:
            ui.label("Лидерборд пуст. Запишите Oxford-дельту опубликованного эпизода ниже.")\
                .classes("text-grey")

    table()

    # --- Oxford delta entry -------------------------------------------------
    episodes = {e.slug: e for e in list_saved_episodes(cfg)}
    with ui.card().classes("w-full q-mt-md"):
        ui.label("Запись Oxford-дельты (после публикации эпизода)").classes("font-bold")
        if not episodes:
            ui.label("Нет сохранённых эпизодов. Соберите эпизод в Studio.").classes("text-grey")
        else:
            opts = {s: f"{s} · {e.thesis[:48]} · {e.status.value}" for s, e in episodes.items()}
            ep_sel = ui.select(opts, label="Эпизод", value=next(iter(opts))).classes("w-full")
            with ui.row():
                ab = ui.number("agree before %", value=40, min=0, max=100).classes("w-36")
                aa = ui.number("agree after %", value=55, min=0, max=100).classes("w-36")
                vb = ui.number("votes before", value=120, min=0).classes("w-36")
            with ui.row():
                db_ = ui.number("disagree before %", value=35, min=0, max=100).classes("w-36")
                da = ui.number("disagree after %", value=40, min=0, max=100).classes("w-36")
                va = ui.number("votes after", value=110, min=0).classes("w-36")
            preview = ui.label("").classes("text-blue text-sm")

            def _delta() -> OxfordDelta:
                e = episodes[ep_sel.value]
                return OxfordDelta(
                    episode_id=e.id, agree_before=ab.value, agree_after=aa.value,
                    disagree_before=db_.value, disagree_after=da.value,
                    votes_before=int(vb.value), votes_after=int(va.value),
                )

            def _preview():
                d = _delta()
                if d.no_quorum:
                    preview.set_text("no_quorum (< 30 голосов в одном из опросов) — победа не засчитывается")
                else:
                    e = episodes[ep_sel.value]
                    winner = (e.prosecution_model_id if d.winner_side.value == "prosecution"
                              else e.defense_model_id)
                    preview.set_text(
                        f"winner: {winner} ({d.winner_side.value}; "
                        f"Δpros={d.delta_prosecution:+.0f}, Δdef={d.delta_defense:+.0f})")
            for w in (ep_sel, ab, aa, vb, db_, da, va):
                w.on_value_change(lambda _: _preview())
            _preview()

            def _record():
                e = episodes[ep_sel.value]
                d = _delta()
                service.record_delta(e, d)
                e.status = EpisodeStatus.PUBLISHED
                save_episode(e, cfg)
                entries = service.recompute_and_persist(cfg)
                table.refresh()
                if d.no_quorum:
                    ui.notify("Записано как no_quorum — победа не засчитана, лидерборд пересчитан.",
                              type="warning")
                else:
                    ui.notify(f"Дельта записана, лидерборд пересчитан ({len(entries)} моделей).",
                              type="positive")
            ui.button("Записать и пересчитать", on_click=_record).props("color=primary")

    # --- Export -------------------------------------------------------------
    with ui.card().classes("w-full q-mt-md"):
        ui.label("Экспорт").classes("font-bold")
        published = [e for e in episodes.values() if (e.leaderboard_result or {}).get("delta")]
        with ui.row().classes("items-center gap-3"):
            season = ui.number("season", value=1, min=1, max=2).classes("w-28")
            ep_no = ui.number("episode no", value=max(1, len(published)), min=1).classes("w-28")
            total = ui.number("total", value=10, min=1).classes("w-28")

        def _export():
            entries = service.recompute_and_persist(cfg)
            res = service.export_files(
                cfg, entries, season=int(season.value), episode_no=int(ep_no.value),
                total=int(total.value))
            ui.download(Path(res["md"]).read_bytes(), f"leaderboard_s{int(season.value)}.md")
            ui.download(Path(res["csv"]).read_bytes(), f"leaderboard_s{int(season.value)}.csv")
            ui.notify(f"Экспортировано: {res['md']} + .csv", type="positive")
        ui.button("Экспорт Markdown + CSV", on_click=_export).props("color=primary")
