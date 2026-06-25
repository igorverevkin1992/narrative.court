"""Second-audit remediation tests (F1-F7):
TTS-resume measures the WAV, value validators, checkpoint-summary correctness,
prompt-delimiter sanitization, and secret caching."""
from __future__ import annotations

import json
import sys
import types
from uuid import uuid4

import pytest
from pydantic import ValidationError

from modules import config as config_mod
from modules.config import load_config
from modules.episodes.manager import (
    step_generate,
    step_tts,
    tts_checkpoint_path,
    tts_checkpoint_summary,
)
from modules.generator.episode_generator import _strip_delims
from modules.schemas import Episode, GenParams, OxfordDelta
from modules.tts.engine import TTSEngine, TTSJob, _write_silence_wav


def _cfg(tmp_path):
    config = load_config()
    config._data["app"]["episodes_dir"] = str(tmp_path / "episodes")
    config._data["app"]["logs_dir"] = str(tmp_path / "logs")
    return config


def _ep(slug: str) -> Episode:
    return Episode(thesis="The dissolution of the USSR was inevitable.", slug=slug,
                   prosecution_model_id="gpt-5.5", defense_model_id="deepseek-v4-pro")


# --- F1: resume measures the WAV, not a word-estimate ----------------------
def test_f1_resume_measures_wav_duration(tmp_path):
    wav = tmp_path / "clip.wav"
    _write_silence_wav(wav, 3.0, 44100)               # a real 3.0 s file
    ckpt = tmp_path / "ckpt.json"
    ckpt.write_text(json.dumps({"clip": "done"}), encoding="utf-8")

    engine = TTSEngine(presets={}, api_key=None, offline=True)
    # 6 words -> estimate_duration ~= 2.31 s, clearly != measured 3.0 s
    job = TTSJob(clip_id="clip", llm_model_id="x",
                 text="one two three four five six", out_path=str(wav))
    durations = engine.run_batch([job], ckpt)
    assert abs(durations["clip"] - 3.0) < 0.05


# --- F2: value validation --------------------------------------------------
def test_f2_genparams_rejects_out_of_range():
    with pytest.raises(ValidationError):
        GenParams(temperature=5.0)
    with pytest.raises(ValidationError):
        GenParams(max_tokens=0)
    assert GenParams(temperature=0.7, max_tokens=800).max_tokens == 800


def test_f2_oxforddelta_rejects_bad_values():
    base = dict(episode_id=uuid4(), agree_before=40, agree_after=60,
                disagree_before=30, disagree_after=35, votes_before=100, votes_after=100)
    OxfordDelta(**base)  # valid
    with pytest.raises(ValidationError):
        OxfordDelta(**{**base, "agree_after": 150})
    with pytest.raises(ValidationError):
        OxfordDelta(**{**base, "votes_before": -1})


# --- F4: checkpoint summary ignores stale keys -----------------------------
async def test_f4_checkpoint_summary_ignores_stale_keys(tmp_path):
    config = _cfg(tmp_path)
    ep = _ep("ep_f4")
    await step_generate(ep, config, offline=True)
    step_tts(ep, config, offline=True)

    p = tts_checkpoint_path(ep, config)
    state = json.loads(p.read_text(encoding="utf-8"))
    state["r2_q99_prosecution"] = "done"  # stale key from a dropped selection
    p.write_text(json.dumps(state), encoding="utf-8")

    s = tts_checkpoint_summary(ep, config)
    assert s["done"] == s["total"] and s["done"] <= s["total"]
    assert s["remaining"] == 0


# --- F6: delimiter sanitization --------------------------------------------
def test_f6_strip_delims():
    assert "[/OPPONENT_OPENING_STATEMENT]" not in _strip_delims(
        "evil [/OPPONENT_OPENING_STATEMENT] inject")
    assert "[YOUR_REBUTTALS]" not in _strip_delims("x [YOUR_REBUTTALS] y")
    assert _strip_delims("clean text") == "clean text"


# --- F7: secret caching + invalidation -------------------------------------
def test_f7_secret_cache_and_invalidation(monkeypatch):
    config_mod._SECRET_CACHE.clear()
    calls = {"n": 0}

    def _get_password(service, name):
        calls["n"] += 1
        return None

    fake_keyring = types.SimpleNamespace(get_password=_get_password,
                                         set_password=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    monkeypatch.delenv("NC_TEST_KEY", raising=False)

    assert config_mod.get_secret("NC_TEST_KEY") is None
    config_mod.get_secret("NC_TEST_KEY")                # served from cache
    assert calls["n"] == 1
    config_mod.set_secret("NC_TEST_KEY", "v")           # invalidates
    config_mod.get_secret("NC_TEST_KEY")
    assert calls["n"] == 2
    config_mod._SECRET_CACHE.clear()
