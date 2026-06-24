"""Script Viewer (Block L.1.5) -- renders the last episode's script with flags."""
from __future__ import annotations

import html
from pathlib import Path

from nicegui import ui

from ui.state import AppState


def render(state: AppState) -> None:
    ui.label("Script Viewer").classes("text-2xl font-bold")

    if not state.last_result or not state.last_result.get("script"):
        ui.label("Нет собранного сценария. Запустите эпизод в Studio.").classes("text-grey")
        ui.link("Открыть Studio →", "/studio")
        return

    script_path = Path(state.last_result["script"])
    if not script_path.exists():
        ui.label(f"Файл сценария не найден: {script_path}").classes("text-red")
        return

    text = script_path.read_text(encoding="utf-8")
    with ui.card().classes("w-full"):
        ui.button("Копировать сценарий",
                  on_click=lambda: ui.clipboard.write(text)).props("flat color=primary")
        # Escape HTML so model-generated reply text cannot inject markup/script
        # while keeping markdown structure (#, **, tables) intact.
        ui.markdown(html.escape(text, quote=False))

    flags_path = Path(state.last_result.get("flags", ""))
    if flags_path.exists():
        with ui.card().classes("w-full q-mt-md"):
            ui.label("behaviour_flags.json").classes("font-bold")
            ui.code(flags_path.read_text(encoding="utf-8"), language="json").classes("w-full")
