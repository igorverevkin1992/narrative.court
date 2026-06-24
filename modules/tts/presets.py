"""Load the 15 ElevenLabs voice presets from config/tts_presets.json."""
from __future__ import annotations

import json
from pathlib import Path

from modules.schemas import TTSPreset


def load_presets(path: str | Path) -> dict[str, TTSPreset]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    presets = {}
    for p in data.get("presets", []):
        presets[p["llm_model_id"]] = TTSPreset(**p)
    return presets
