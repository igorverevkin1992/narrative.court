"""Episode Planner (Block L.1.3) -- season grid of episodes with reordering.

Episodes are read from the JSON store (the source of truth). The operator can
reorder the running order with up/down controls; the order persists to
data/episodes/_planner_order.json so the plan survives restarts.
"""
from __future__ import annotations

import json
from pathlib import Path

from nicegui import ui

from modules.episodes.manager import list_saved_episodes
from ui.components.progress import status_badge
from ui.state import AppState


def _order_path(cfg) -> Path:
    return cfg.resolve_path("episodes_dir") / "_planner_order.json"


def _load_order(cfg) -> list[str]:
    p = _order_path(cfg)
    if p.exists():
        try:
            return list(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return []
    return []


def _save_order(cfg, slugs: list[str]) -> None:
    p = _order_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(slugs, indent=2), encoding="utf-8")


def render(state: AppState) -> None:
    cfg = state.config
    ui.label("Episode Planner").classes("text-2xl font-bold")
    ui.label("Сезонная сетка эпизодов. Порядок (▲▼) сохраняется между запусками.")\
        .classes("text-sm text-grey")

    by_slug = {e.slug: e for e in list_saved_episodes(cfg)}
    if not by_slug:
        ui.label("Пока нет эпизодов. Создайте первый в Studio.").classes("text-grey")
        ui.link("Открыть Studio →", "/studio")
        return

    # Ordered list: saved order first (known slugs), then any new episodes.
    order = [s for s in _load_order(cfg) if s in by_slug]
    order += [s for s in by_slug if s not in order]

    @ui.refreshable
    def grid() -> None:
        for i, slug in enumerate(order):
            e = by_slug[slug]
            with ui.card().classes("w-full q-my-xs"):
                with ui.row().classes("items-center justify-between w-full"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(f"{i + 1}.").classes("text-grey w-6")
                        ui.label(f"{e.slug} — {e.thesis[:60]}").classes("text-sm")
                    with ui.row().classes("items-center gap-1"):
                        ui.badge(f"{e.prosecution_model_id} vs {e.defense_model_id}", color="blue")
                        status_badge(e.status.value)

                        def _up(idx=i):
                            if idx > 0:
                                order[idx - 1], order[idx] = order[idx], order[idx - 1]
                                _save_order(cfg, order)
                                grid.refresh()

                        def _down(idx=i):
                            if idx < len(order) - 1:
                                order[idx + 1], order[idx] = order[idx], order[idx + 1]
                                _save_order(cfg, order)
                                grid.refresh()
                        ui.button(icon="arrow_upward", on_click=_up).props("flat dense").classes("text-grey")
                        ui.button(icon="arrow_downward", on_click=_down).props("flat dense").classes("text-grey")

    grid()

    # --- Batch run (I7) -----------------------------------------------------
    with ui.card().classes("w-full q-mt-md"):
        ui.label("Пакетный прогон по плану (I7)").classes("font-bold")
        b_offline = ui.checkbox("Offline (mock)", value=True)
        b_log = ui.log(max_lines=200).classes("w-full h-40 bg-black text-green-400 text-xs")
        b_btn = ui.button("Прогнать все эпизоды по порядку").props("color=primary")

        async def _batch():
            from modules.episodes.manager import run_batch_episodes
            b_btn.disable()
            b_log.clear()
            eps = [by_slug[s] for s in order]
            b_log.push(f"Старт: {len(eps)} эпизод(ов), offline={b_offline.value}")
            try:
                results = await run_batch_episodes(eps, cfg, offline=b_offline.value,
                                                   on_log=lambda m: b_log.push(m))
            except Exception as exc:
                b_log.push(f"ОШИБКА батча: {exc}")
                b_btn.enable()
                return
            ok = sum(1 for r in results if r.get("ok"))
            b_log.push(f"Готово: {ok}/{len(results)} успешно")
            ui.notify(f"Батч завершён: {ok}/{len(results)}", type="positive")
            grid.refresh()
            b_btn.enable()
        b_btn.on_click(_batch)
