"""The Narrative Court -- local web app entry point.

Loads config, ensures data dirs + SQLite schema, seeds the model registry, wires
the NiceGUI UI, and launches the local server. No Docker, no cloud.

Run:  python main.py
"""
from __future__ import annotations

from nicegui import ui

from modules.config import is_first_run, load_config
from modules.db import init_db, seed_models_from_config
from ui import app as ui_app


def bootstrap():
    config = load_config()
    for key in ("episodes_dir", "exports_dir", "logs_dir"):
        config.resolve_path(key).mkdir(parents=True, exist_ok=True)

    db_path = config.resolve_path("db_path")
    first_run = is_first_run(db_path)
    init_db(db_path)
    seed_models_from_config(config.models)

    ui_app.init(config)
    if first_run:
        print("[onboarding] First run detected: DB created and models seeded. "
              "Add API keys on the Config screen (or via keyring/.env) before live generation.")
    return config


config = bootstrap()

if __name__ in {"__main__", "__mp_main__"}:
    app_cfg = config.app
    ui.run(
        host=app_cfg.get("host", "127.0.0.1"),
        port=int(app_cfg.get("port", 8080)),
        title="The Narrative Court",
        reload=False,
        show=bool(app_cfg.get("open_browser_on_start", False)),
    )
