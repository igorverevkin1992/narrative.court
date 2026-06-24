"""Topic Bank (Block L.1.2 / J) -- CRUD + interactive 6-criteria checklist.

The checklist auto-recommendation (approved / warning / rejected) is computed
live from the same rule as TopicChecklist.model_validator. Saved topics persist
in the SQLite topics table; the table supports status edit, filtering, delete.
"""
from __future__ import annotations

from nicegui import ui

from modules.schemas import Topic, TopicChecklist, TopicStatus
from modules.topics.bank import list_topics, save_topic
from modules.topics.checklist import evaluate
from ui.components.progress import status_badge
from ui.state import AppState

_RISK_COLORS = {"green": "green", "yellow": "orange", "red": "red",
                "low": "green", "medium": "teal", "high": "orange", "guaranteed_refused": "red"}


def render(state: AppState) -> None:
    cfg = state.config
    model_opts = {m["id"]: m["display_name"] for m in cfg.models}
    ui.label("Topic Bank").classes("text-2xl font-bold")

    # ----------------------------------------------------------- create form
    with ui.card().classes("w-full"):
        ui.label("Новая тема + чек-лист утверждения").classes("font-bold")
        thesis = ui.input("Тезис").classes("w-full")
        variants = ui.input("Варианты формулировки (до 3, через ;)").classes("w-full")
        with ui.row().classes("w-full"):
            cat = ui.select(["safe", "optimal", "hot"], label="Категория", value="optimal").classes("w-40")
            mon_risk = ui.select(["green", "yellow", "red"], label="Monetization", value="green").classes("w-40")
            ds_risk = ui.select(["low", "medium", "high", "guaranteed_refused"],
                                label="DeepSeek risk", value="low").classes("w-52")
        with ui.row().classes("w-full"):
            pair_p = ui.select(model_opts, label="Recommended Prosecution", value="gpt-5.5").classes("w-64")
            pair_d = ui.select(model_opts, label="Recommended Defense", value="deepseek-v4-pro").classes("w-64")
        anchors = ui.textarea("Factual anchors (по одному на строку)").classes("w-full")
        with ui.row().classes("items-center gap-4"):
            evidence = ui.checkbox("Доказательства для обеих сторон", value=True)
            debatable = ui.checkbox("Спорный (не ложная симметрия)", value=True)
            reach = ui.number("Reach vs Safety (1-5)", value=3, min=1, max=5).classes("w-44")
            fresh = ui.number("Freshness vs Evergreen (1-5)", value=3, min=1, max=5).classes("w-44")
        notes = ui.textarea("Заметки").classes("w-full")

        with ui.row().classes("items-center gap-2"):
            ui.label("Авто-статус:").classes("font-bold")
            badge = ui.badge("warning", color="orange").classes("text-lg")

        def _status() -> str:
            return evaluate(
                has_evidence_both_sides=evidence.value, is_debatable=debatable.value,
                deepseek_risk=ds_risk.value, monetization_risk=mon_risk.value,
                reach_vs_safety=int(reach.value), freshness_vs_evergreen=int(fresh.value))

        def _recompute():
            s = _status()
            badge.set_text(s)
            badge.props(f"color={ {'approved':'green','warning':'orange','rejected':'red'}[s] }")
        for w in (evidence, debatable, ds_risk, mon_risk, reach, fresh):
            w.on_value_change(lambda _: _recompute())
        _recompute()

        def _save():
            if not thesis.value.strip():
                ui.notify("Укажите тезис", type="warning")
                return
            if pair_p.value == pair_d.value:
                ui.notify("Пара моделей должна быть разной", type="warning")
                return
            checklist = TopicChecklist(
                has_evidence_both_sides=evidence.value, is_debatable=debatable.value,
                deepseek_risk=ds_risk.value, monetization_risk=mon_risk.value,
                reach_vs_safety=int(reach.value), freshness_vs_evergreen=int(fresh.value))
            topic = Topic(
                thesis=thesis.value.strip(),
                thesis_variants=[v.strip() for v in variants.value.split(";") if v.strip()][:3],
                category=cat.value, monetization_risk=mon_risk.value, deepseek_risk=ds_risk.value,
                recommended_pair=(pair_p.value, pair_d.value),
                factual_anchors=[a.strip() for a in anchors.value.splitlines() if a.strip()],
                checklist=checklist, notes=notes.value.strip(),
                status=TopicStatus.REJECTED if checklist.auto_status == "rejected" else TopicStatus.DRAFT)
            try:
                save_topic(topic)
            except Exception as exc:
                ui.notify(f"Не удалось сохранить (БД?): {exc}", type="negative")
                return
            thesis.value = ""
            ui.notify(f"Тема сохранена (auto: {checklist.auto_status})", type="positive")
            bank_table.refresh()
        ui.button("Сохранить тему", on_click=_save).props("color=primary")

    # ----------------------------------------------------------- topics table
    with ui.row().classes("items-center gap-3 q-mt-md"):
        ui.label("Сохранённые темы").classes("font-bold")
        flt = ui.select(["all", "draft", "approved", "scheduled", "completed", "rejected"],
                        label="Фильтр статуса", value="all").classes("w-48")

    @ui.refreshable
    def bank_table() -> None:
        try:
            topics = list_topics()
        except Exception:
            topics = []
        if flt.value and flt.value != "all":
            topics = [t for t in topics if t.status.value == flt.value]
        if not topics:
            ui.label("Тем пока нет (или не совпадает фильтр).").classes("text-grey")
            return
        for t in topics:
            with ui.card().classes("w-full q-my-xs"):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label(t.thesis[:90]).classes("text-sm")
                    with ui.row().classes("items-center gap-1"):
                        ui.badge(t.category, color="blue")
                        ui.badge(f"mon:{t.monetization_risk}", color=_RISK_COLORS.get(t.monetization_risk, "grey"))
                        ui.badge(f"ds:{t.deepseek_risk}", color=_RISK_COLORS.get(t.deepseek_risk, "grey"))
                        status_badge(t.checklist.auto_status)
                with ui.row().classes("items-center gap-2"):
                    st = ui.select(["draft", "approved", "scheduled", "completed", "rejected"],
                                   value=t.status.value, label="status").classes("w-44")

                    def _set_status(e, topic=t):
                        topic.status = TopicStatus(e.value)
                        try:
                            save_topic(topic)
                            ui.notify("Статус обновлён", type="positive")
                        except Exception as exc:
                            ui.notify(f"Ошибка: {exc}", type="negative")
                    st.on_value_change(_set_status)

                    def _delete(topic_id=str(t.id)):
                        from modules.db import delete_topic
                        delete_topic(topic_id)
                        ui.notify("Тема удалена", type="info")
                        bank_table.refresh()
                    ui.button(icon="delete", on_click=_delete).props("flat color=negative dense")

    flt.on_value_change(lambda _: bank_table.refresh())
    bank_table()
