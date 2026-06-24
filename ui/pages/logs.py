"""Logs screen (Block L.1.8) -- lists generation log files (all runs are logged)."""
from __future__ import annotations

from pathlib import Path

from nicegui import ui

from ui.state import AppState


def render(state: AppState) -> None:
    cfg = state.config
    ui.label("Logs").classes("text-2xl font-bold")
    logs_dir = cfg.resolve_path("logs_dir")
    ui.label(f"Каталог логов: {logs_dir}").classes("text-sm text-grey")

    files = sorted(logs_dir.rglob("*.jsonl")) if logs_dir.exists() else []
    if not files:
        ui.label("Логи пока пусты (политика отбора: первый валидный прогон).").classes("text-grey")
        return

    options = {str(p): str(p.relative_to(logs_dir)) for p in files}
    selected = ui.select(options, label="Файл лога", value=str(files[0])).classes("w-full")
    viewer = ui.code("", language="json").classes("w-full")

    def show():
        p = Path(selected.value)
        lines = p.read_text(encoding="utf-8").splitlines()[-20:]
        viewer.set_content("\n".join(lines))

    selected.on_value_change(lambda _: show())
    show()
