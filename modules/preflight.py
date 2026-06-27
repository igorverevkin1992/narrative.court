"""Live preflight checks for the production MVP.

Cheap REAL calls that validate the configured API keys, model names, and voice
presets actually work *before* the operator burns a full episode. Each check is
isolated and returns a structured ``{target, ok, detail}`` dict so the UI/CLI can
render a green/red checklist. Sanctioned models are skipped with the legal note.
"""
from __future__ import annotations

from pathlib import Path

from modules.config import ROOT, Config, get_secret
from modules.llm.orchestrator import Orchestrator


def check_model(config: Config, model_id: str) -> dict:
    """One minimal live generate call against a model."""
    mc = config.model(model_id)
    if mc is None:
        return {"target": model_id, "ok": False, "detail": "model not in config"}
    if mc.get("sanctions_risk"):
        return {"target": model_id, "ok": False,
                "detail": "[SANCTIONS RISK — legal review required, INA §329] skipped"}
    key_env = mc.get("api_key_env", "")
    if not get_secret(key_env):
        return {"target": model_id, "ok": False, "detail": f"missing key {key_env}"}
    try:
        orch = Orchestrator(config, offline=False)
        res = orch.generate(model_id, "You are a preflight test.",
                            "Reply with the single word OK.", 0.0, 8)
        ok = bool((res.content or "").strip())
        return {"target": model_id, "ok": ok,
                "detail": ((res.content or "").strip()[:60] or "empty response"),
                "model_version": res.model_version}
    except Exception as exc:
        return {"target": model_id, "ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def check_elevenlabs(config: Config) -> dict:
    """Validate the ElevenLabs key is present and the SDK is importable."""
    if not get_secret("ELEVENLABS_API_KEY"):
        return {"target": "ElevenLabs", "ok": False, "detail": "missing ELEVENLABS_API_KEY"}
    try:
        import elevenlabs  # noqa: F401
    except Exception as exc:
        return {"target": "ElevenLabs", "ok": False, "detail": f"SDK not installed: {exc}"}
    return {"target": "ElevenLabs", "ok": True, "detail": "key present, SDK importable"}


def check_voice_presets(config: Config, model_ids: list[str]) -> dict:
    """Flag TTS presets that are missing or still hold placeholder voice ids."""
    from modules.tts.presets import load_presets

    rel = config.tts_defaults.get("presets_file", "./config/tts_presets.json")
    path = Path(rel)
    if not path.is_absolute():
        path = ROOT / rel.lstrip("./")
    try:
        presets = load_presets(path)
    except Exception as exc:
        return {"target": "voice presets", "ok": False, "detail": str(exc)}
    problems: list[str] = []
    for mid in model_ids:
        pr = presets.get(mid)
        if pr is None:
            problems.append(f"{mid}: no preset")
        elif pr.el_voice_id.upper().startswith("REPLACE"):
            problems.append(f"{mid}: placeholder voice_id")
    if problems:
        return {"target": "voice presets", "ok": False, "detail": "; ".join(problems)}
    return {"target": "voice presets", "ok": True, "detail": "real voice ids set"}


def preflight_pair(config: Config, prosecution_id: str, defense_id: str) -> list[dict]:
    """Full preflight for a production pair: both models + ElevenLabs + voices."""
    return [
        check_model(config, prosecution_id),
        check_model(config, defense_id),
        check_elevenlabs(config),
        check_voice_presets(config, [prosecution_id, defense_id]),
    ]
