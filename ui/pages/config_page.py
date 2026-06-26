"""Config screen (Block L.1.7) -- models, masked keys, in-app key entry,
TTS presets, paths. Keys are stored in the OS keyring (never in the repo)."""
from __future__ import annotations

from nicegui import ui

from modules.config import get_secret, mask, set_secret
from modules.generator.episode_generator import PROMPTS_DIR, load_prompt
from ui.state import AppState


def render(state: AppState) -> None:
    cfg = state.config
    ui.label("Config").classes("text-2xl font-bold")

    # --- Models + masked keys (refreshable so key edits show immediately) ----
    with ui.card().classes("w-full"):
        ui.label("Модели и API-ключи (masked)").classes("font-bold")
        columns = [
            {"name": "id", "label": "Model", "field": "id", "align": "left"},
            {"name": "fmt", "label": "API format", "field": "fmt"},
            {"name": "season", "label": "Season", "field": "season"},
            {"name": "env", "label": "Key env", "field": "env"},
            {"name": "key", "label": "Key (masked)", "field": "key"},
            {"name": "flags", "label": "Flags", "field": "flags"},
        ]

        @ui.refreshable
        def keys_table() -> None:
            rows = []
            for m in cfg.models:
                flags = []
                if m.get("translation_layer"):
                    flags.append("translation")
                if m.get("sanctions_risk"):
                    flags.append("SANCTIONS")
                rows.append({
                    "id": m["display_name"], "fmt": m["api_format"], "season": m["season"],
                    "env": m["api_key_env"], "key": mask(get_secret(m["api_key_env"])),
                    "flags": ", ".join(flags) or "-",
                })
            ui.table(columns=columns, rows=rows, row_key="env").classes("w-full")

        keys_table()
        ui.label("Ключи хранятся в OS keyring (Keychain / Credential Manager / "
                 "SecretService) или config/.env. В репозиторий не попадают.")\
            .classes("text-xs text-grey")

    # --- In-app key entry (writes to keyring) -------------------------------
    with ui.card().classes("w-full q-mt-md"):
        ui.label("Установить / обновить ключ").classes("font-bold")
        env_names = sorted({m["api_key_env"] for m in cfg.models} | {"ELEVENLABS_API_KEY"})
        with ui.row().classes("items-center gap-2 w-full"):
            env_sel = ui.select(env_names, label="Ключ", value=env_names[0]).classes("w-72")
            val_in = ui.input("Значение", password=True, password_toggle_button=True).classes("w-96")

            def _save_key():
                v = (val_in.value or "").strip()
                if not v:
                    ui.notify("Пустое значение", type="warning")
                    return
                if set_secret(env_sel.value, v):
                    val_in.value = ""
                    keys_table.refresh()
                    ui.notify(f"{env_sel.value} сохранён в keyring", type="positive")
                else:
                    ui.notify("keyring недоступен — задайте ключ в config/.env", type="negative")
            ui.button("Сохранить в keyring", on_click=_save_key).props("color=primary")
        ui.label("Кросс-платформенно, одним кодом. Если keyring недоступен (headless "
                 "Linux) — используйте config/.env (см. .env.example).").classes("text-xs text-grey")

    # --- TTS ----------------------------------------------------------------
    with ui.card().classes("w-full q-mt-md"):
        ui.label("TTS").classes("font-bold")
        ui.label(f"ElevenLabs key: {mask(get_secret('ELEVENLABS_API_KEY'))}")
        ui.label(f"Пресеты: {cfg.tts_defaults.get('presets_file')}")
        ui.label(f"Формат: {cfg.tts_defaults.get('output_format')} · "
                 f"модель: {cfg.tts_defaults.get('el_model_id')}")

    # --- System prompts editor (I5) -----------------------------------------
    with ui.card().classes("w-full q-mt-md"):
        ui.label("Системные промпты (anti-hedge) — редактирование (I5)").classes("font-bold")
        prompt_opts = {"prosecution_s1": "Prosecution / S1", "defense_s1": "Defense / S1",
                       "prosecution_s2": "Prosecution / S2", "defense_s2": "Defense / S2"}
        psel = ui.select(prompt_opts, value="prosecution_s1").classes("w-72")
        editor = ui.textarea("").props("rows=12").classes("w-full")

        def _load_prompt():
            side, season = psel.value.rsplit("_s", 1)
            editor.value = load_prompt(side, int(season))

        def _save_prompt():
            if "{thesis}" not in editor.value:
                ui.notify("Промпт должен содержать плейсхолдер {thesis}", type="warning")
                return
            side, season = psel.value.rsplit("_s", 1)
            (PROMPTS_DIR / f"{side}_s{season}.txt").write_text(editor.value, encoding="utf-8")
            ui.notify("Промпт сохранён", type="positive")
        psel.on_value_change(lambda _: _load_prompt())
        _load_prompt()
        ui.button("Сохранить промпт", on_click=_save_prompt).props("color=primary")

    # --- Paths --------------------------------------------------------------
    with ui.card().classes("w-full q-mt-md"):
        ui.label("Пути").classes("font-bold")
        for key in ("episodes_dir", "exports_dir", "logs_dir", "db_path"):
            ui.label(f"{key}: {cfg.resolve_path(key)}")
