"""Logs screen (Block L.1.8 / B.3) -- every generation run is logged (JSONL).

Parses GenerationLog records from data/logs/<episode>/<model>.jsonl, filters by
episode / model / finish_reason, shows each run's full prompt + result, and lets
the operator export a chosen run as the 'selected' variant.
"""
from __future__ import annotations

import json
from pathlib import Path

from nicegui import ui

from ui.state import AppState


def _load_entries(logs_dir: Path) -> list[dict]:
    entries: list[dict] = []
    if not logs_dir.exists():
        return entries
    for p in sorted(logs_dir.rglob("*.jsonl")):
        episode = p.parent.name
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            rec["_episode"] = episode
            rec["_finish"] = (rec.get("result") or {}).get("finish_reason", "?")
            entries.append(rec)
    return entries


def render(state: AppState) -> None:
    cfg = state.config
    logs_dir = cfg.resolve_path("logs_dir")
    exports_dir = cfg.resolve_path("exports_dir")
    ui.label("Logs").classes("text-2xl font-bold")
    ui.label(f"Каталог логов: {logs_dir} · политика отбора: first_valid (логируются ВСЕ прогоны)")\
        .classes("text-sm text-grey")

    entries = _load_entries(logs_dir)
    if not entries:
        ui.label("Логи пока пусты. Запустите генерацию в Studio.").classes("text-grey")
        return

    episodes = ["all"] + sorted({e["_episode"] for e in entries})
    models = ["all"] + sorted({e.get("model_id", "?") for e in entries})
    finishes = ["all"] + sorted({e["_finish"] for e in entries})

    with ui.row().classes("items-center gap-3"):
        f_ep = ui.select(episodes, label="Эпизод", value="all").classes("w-64")
        f_model = ui.select(models, label="Модель", value="all").classes("w-52")
        f_fin = ui.select(finishes, label="finish_reason", value="all").classes("w-44")
    count_lbl = ui.label("").classes("text-xs text-grey")

    @ui.refreshable
    def results() -> None:
        rows = [
            e for e in entries
            if (f_ep.value in ("all", e["_episode"]))
            and (f_model.value in ("all", e.get("model_id")))
            and (f_fin.value in ("all", e["_finish"]))
        ]
        count_lbl.text = f"Показано прогонов: {len(rows)} (из {len(entries)})"
        for e in rows[:200]:
            res = e.get("result") or {}
            title = (f"[{e['_episode'][:8]}] {e.get('model_id')} · {e.get('round_id') or '—'} "
                     f"· attempt {e.get('attempt_number')} · {e['_finish']}")
            with ui.expansion(title).classes("w-full"):
                ui.label(f"log_id: {e.get('log_id')} · seed={e.get('seed')} "
                         f"· temp={e.get('temperature')} · selected={e.get('selected')} "
                         f"· policy={e.get('selection_policy')}").classes("text-xs text-grey")
                ui.label("system_prompt").classes("font-bold text-xs q-mt-xs")
                ui.label(e.get("system_prompt", "")).classes("text-xs whitespace-pre-wrap")
                ui.label("user_prompt").classes("font-bold text-xs q-mt-xs")
                ui.label(e.get("user_prompt", "")).classes("text-xs whitespace-pre-wrap")
                ui.label("content").classes("font-bold text-xs q-mt-xs")
                ui.label(res.get("content", "")).classes("text-sm whitespace-pre-wrap")
                if res.get("reasoning_content"):
                    ui.label("reasoning_content").classes("font-bold text-xs q-mt-xs")
                    ui.label(res["reasoning_content"]).classes("text-xs whitespace-pre-wrap text-grey")

                def _export(entry=e):
                    exports_dir.mkdir(parents=True, exist_ok=True)
                    name = (f"{entry['_episode'][:8]}_{entry.get('round_id') or 'rnd'}_"
                            f"{entry.get('model_id')}_{str(entry.get('log_id'))[:8]}.selected.json")
                    out = exports_dir / name
                    out.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
                    ui.notify(f"Экспортирован как selected: {out}", type="positive")
                ui.button("Экспортировать как selected", on_click=_export)\
                    .props("flat color=primary dense")

    for w in (f_ep, f_model, f_fin):
        w.on_value_change(lambda _: results.refresh())
    results()
