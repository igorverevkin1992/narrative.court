"""Topic Bank (Block L.1.2) -- interactive approval checklist preview."""
from __future__ import annotations

from nicegui import ui

from modules.topics.checklist import evaluate
from ui.state import AppState


def render(state: AppState) -> None:
    ui.label("Topic Bank").classes("text-2xl font-bold")

    with ui.card().classes("w-full"):
        ui.label("Чек-лист утверждения темы (авто-рекомендация)").classes("font-bold")
        thesis = ui.input("Тезис").classes("w-full")
        evidence = ui.checkbox("Есть доказательства для обеих сторон", value=True)
        debatable = ui.checkbox("Спорный (не ложная симметрия)", value=True)
        with ui.row():
            ds_risk = ui.select(
                ["low", "medium", "high", "guaranteed_refused"],
                label="DeepSeek risk", value="low",
            ).classes("w-48")
            mon_risk = ui.select(
                ["green", "yellow", "red"], label="Monetization risk", value="green",
            ).classes("w-48")
        with ui.row():
            reach = ui.number("Reach vs Safety (1-5)", value=3, min=1, max=5).classes("w-48")
            fresh = ui.number("Freshness vs Evergreen (1-5)", value=3, min=1, max=5).classes("w-48")

        badge = ui.badge("warning", color="orange").classes("text-lg")

        def recompute():
            status = evaluate(
                has_evidence_both_sides=evidence.value,
                is_debatable=debatable.value,
                deepseek_risk=ds_risk.value,
                monetization_risk=mon_risk.value,
                reach_vs_safety=int(reach.value),
                freshness_vs_evergreen=int(fresh.value),
            )
            color = {"approved": "green", "warning": "orange", "rejected": "red"}[status]
            badge.set_text(status)
            badge.props(f"color={color}")

        for w in (evidence, debatable, ds_risk, mon_risk, reach, fresh):
            w.on_value_change(lambda _: recompute())
        recompute()

    ui.label("Полный CRUD банка тем и фильтры — Фаза 3 (см. ТЗ Раздел 8).").classes("text-xs text-grey q-mt-md")
