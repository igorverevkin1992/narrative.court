"""Episode Studio (Module 13 / Block L) -- the MVP working flow.

Select topic + model pair -> run the pipeline (offline mock or live) with a
streaming log -> see the produced episode folder, FCPXML/EDL, and script.
"""
from __future__ import annotations

from nicegui import ui

from ui.state import AppState


def render(state: AppState) -> None:
    cfg = state.config
    ui.label("Episode Studio").classes("text-2xl font-bold q-mb-md")
    ui.label("Шаги 1-9: выбор темы и пары -> генерация -> TTS -> экспорт таймлайна").classes("text-sm text-grey")

    model_opts = {m["id"]: m["display_name"] for m in cfg.models}
    sanctioned = {m["id"] for m in cfg.models if m.get("sanctions_risk")}

    with ui.card().classes("w-full q-mt-md"):
        ui.label("Шаг 1-2. Тема, пара моделей, настройки").classes("font-bold")
        thesis = ui.input("Тезис эпизода",
                          value="The dissolution of the USSR was inevitable.").classes("w-full")
        slug = ui.input("Slug (имя папки, [a-z0-9_])", value="ep001_dissolution_ussr").classes("w-full")
        with ui.row().classes("w-full"):
            pros = ui.select(model_opts, label="Prosecution", value="gpt-5.5").classes("w-64")
            deff = ui.select(model_opts, label="Defense", value="deepseek-v4-pro").classes("w-64")
        with ui.row().classes("w-full items-center"):
            temp = ui.number("temperature", value=0.7, min=0, max=1, step=0.1).classes("w-32")
            maxtok = ui.number("max_tokens", value=800, min=64, step=50).classes("w-32")
            seed = ui.number("seed (0=random)", value=42, min=0).classes("w-32")
            offline = ui.checkbox("Offline (mock, без ключей)", value=state.offline_default)

    sanctions_note = ui.markdown("").classes("text-red")

    def _check_sanctions():
        flagged = [m for m in (pros.value, deff.value) if m in sanctioned]
        if flagged:
            sanctions_note.set_content(
                "**[SANCTIONS RISK - legal review required, INA §329]** "
                f"Выбрана модель {flagged[0]}. Проверьте юридическую допустимость использования."
            )
        else:
            sanctions_note.set_content("")

    pros.on_value_change(lambda _: _check_sanctions())
    deff.on_value_change(lambda _: _check_sanctions())

    ui.label("Шаг 4-9. Лог конвейера").classes("font-bold q-mt-md")
    log = ui.log(max_lines=300).classes("w-full h-64 bg-black text-green-400 text-xs")
    result_area = ui.column().classes("w-full q-mt-md")
    run_btn = ui.button("Запустить конвейер").props("color=primary")

    async def run():
        from modules.episodes.manager import run_full_pipeline
        from modules.schemas import Episode, GenParams

        if pros.value == deff.value:
            ui.notify("Prosecution и Defense должны быть разными моделями", type="warning")
            return
        run_btn.disable()
        result_area.clear()
        log.clear()
        log.push("Старт конвейера...")
        episode = Episode(
            thesis=thesis.value, slug=slug.value,
            prosecution_model_id=pros.value, defense_model_id=deff.value,
            gen_params=GenParams(
                temperature=float(temp.value), max_tokens=int(maxtok.value),
                seed=(int(seed.value) or None),
            ),
        )
        try:
            res = await run_full_pipeline(
                episode, cfg, offline=offline.value, on_log=lambda m: log.push(m)
            )
        except Exception as exc:  # surface a Russian-language error + retry
            log.push(f"ОШИБКА: {exc}")
            ui.notify(f"Ошибка конвейера: {exc}", type="negative")
            run_btn.enable()
            return

        state.last_episode = episode
        state.last_result = res
        with result_area:
            ui.label("Готово ✓").classes("text-green-600 text-lg font-bold")
            with ui.card().classes("w-full"):
                ui.label(f"Папка эпизода: {res['episode_dir']}")
                ui.label(f"Клипов: {res['n_clips']} · Флагов поведения: {res['n_flags']}")
                ui.label(f"FCPXML (primary): {res['fcpxml']}")
                ui.label(f"EDL (fallback): {res['edl']}")
                ui.label(f"Маркеры: {res['markers_md']}")
                ui.label(f"Сценарий: {res['script']}")
                ui.link("Открыть сценарий →", "/script")
        ui.notify("Эпизод собран", type="positive")
        run_btn.enable()

    run_btn.on_click(run)
    _check_sanctions()
