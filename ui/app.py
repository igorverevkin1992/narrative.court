"""NiceGUI application shell: header navigation + 8 page routes (Module 13)."""
from __future__ import annotations

from nicegui import app, ui

from modules.config import Config
from ui.pages import (
    config_page,
    dashboard,
    episode_planner,
    episode_studio,
    leaderboard,
    logs,
    script_viewer,
    topic_bank,
)
from ui.state import AppState

state = AppState()

NAV: list[tuple[str, str]] = [
    ("Dashboard", "/"),
    ("Topic Bank", "/topics"),
    ("Planner", "/planner"),
    ("Studio", "/studio"),
    ("Script", "/script"),
    ("Leaderboard", "/leaderboard"),
    ("Config", "/config"),
    ("Logs", "/logs"),
]


def _frame(active: str) -> None:
    with ui.header().classes("items-center justify-between bg-primary"):
        ui.label("The Narrative Court").classes("text-lg font-bold")
        with ui.row().classes("gap-1"):
            for name, route in NAV:
                link = ui.link(name, route).classes("text-white no-underline px-2 py-1 rounded")
                if route == active:
                    link.classes("bg-white/20 font-bold")


def init(config: Config) -> None:
    """Wire config into state and register all routes."""
    state.config = config

    # Serve episode audio so the UI can preview WAVs in-browser (I4).
    media_dir = config.resolve_path("episodes_dir")
    media_dir.mkdir(parents=True, exist_ok=True)
    app.add_media_files("/media", str(media_dir))

    @ui.page("/")
    def _dashboard():
        _frame("/")
        dashboard.render(state)

    @ui.page("/topics")
    def _topics():
        _frame("/topics")
        topic_bank.render(state)

    @ui.page("/planner")
    def _planner():
        _frame("/planner")
        episode_planner.render(state)

    @ui.page("/studio")
    def _studio():
        _frame("/studio")
        episode_studio.render(state)

    @ui.page("/script")
    def _script():
        _frame("/script")
        script_viewer.render(state)

    @ui.page("/leaderboard")
    def _leaderboard():
        _frame("/leaderboard")
        leaderboard.render(state)

    @ui.page("/config")
    def _config():
        _frame("/config")
        config_page.render(state)

    @ui.page("/logs")
    def _logs():
        _frame("/logs")
        logs.render(state)
