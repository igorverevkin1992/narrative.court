"""Config screen (Block L.1.7) -- models, masked keys, TTS presets, paths."""
from __future__ import annotations

from nicegui import ui

from modules.config import get_secret, mask
from ui.state import AppState


def render(state: AppState) -> None:
    cfg = state.config
    ui.label("Config").classes("text-2xl font-bold")

    with ui.card().classes("w-full"):
        ui.label("Модели и API-ключи (masked)").classes("font-bold")
        columns = [
            {"name": "id", "label": "Model", "field": "id"},
            {"name": "fmt", "label": "API format", "field": "fmt"},
            {"name": "season", "label": "Season", "field": "season"},
            {"name": "key", "label": "Key (masked)", "field": "key"},
            {"name": "flags", "label": "Flags", "field": "flags"},
        ]
        rows = []
        for m in cfg.models:
            flags = []
            if m.get("translation_layer"):
                flags.append("translation")
            if m.get("sanctions_risk"):
                flags.append("SANCTIONS")
            rows.append({
                "id": m["display_name"], "fmt": m["api_format"], "season": m["season"],
                "key": mask(get_secret(m["api_key_env"])), "flags": ", ".join(flags) or "-",
            })
        ui.table(columns=columns, rows=rows).classes("w-full")
        ui.label("Ключи хранятся в OS keyring (или config/.env). Здесь не редактируются "
                 "напрямую — используйте onboarding/keyring.").classes("text-xs text-grey")

    with ui.card().classes("w-full q-mt-md"):
        ui.label("TTS").classes("font-bold")
        ui.label(f"ElevenLabs key: {mask(get_secret('ELEVENLABS_API_KEY'))}")
        ui.label(f"Пресеты: {cfg.tts_defaults.get('presets_file')}")
        ui.label(f"Формат: {cfg.tts_defaults.get('output_format')} · "
                 f"модель: {cfg.tts_defaults.get('el_model_id')}")

    with ui.card().classes("w-full q-mt-md"):
        ui.label("Пути").classes("font-bold")
        for key in ("episodes_dir", "exports_dir", "logs_dir", "db_path"):
            ui.label(f"{key}: {cfg.resolve_path(key)}")
