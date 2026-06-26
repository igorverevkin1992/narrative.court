"""Functional-improvement tests: economics (I6), OTIO export (I8),
publish-pack bundle (I9). (I1/I2/I10/I7 covered in test_improvements2.py.)"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from modules.config import load_config
from modules.economics import estimate_episode_cost, tts_cost
from modules.episodes.manager import (
    export_bundle,
    step_export,
    step_generate,
    step_script,
    step_tts,
)
from modules.schemas import Episode, Replica, Side
from modules.timeline import otio_generator
from modules.timeline.exporter import export_timeline
from modules.timeline.timecode_calculator import build_timeline


def _cfg(tmp_path):
    c = load_config()
    c._data["app"]["episodes_dir"] = str(tmp_path / "episodes")
    c._data["app"]["logs_dir"] = str(tmp_path / "logs")
    c._data["app"]["exports_dir"] = str(tmp_path / "exports")
    return c


def _ep(slug: str = "ep_imp") -> Episode:
    return Episode(thesis="The dissolution of the USSR was inevitable.", slug=slug,
                   prosecution_model_id="gpt-5.5", defense_model_id="deepseek-v4-pro")


def _ep_with_clips(slug: str = "ep_otio") -> Episode:
    ep = _ep(slug)
    ep.rounds = {
        "r1_prosecution": [Replica(round_id="r1_prosecution", side=Side.PROSECUTION,
                                   model_id="gpt-5.5", text="a",
                                   audio_path="audio/r1_prosecution.wav", duration_sec=8.0)],
        "r1_defense": [Replica(round_id="r1_defense", side=Side.DEFENSE,
                               model_id="deepseek-v4-pro", text="b",
                               audio_path="audio/r1_defense.wav", duration_sec=10.0)],
    }
    return ep


# --- I6 economics ----------------------------------------------------------
def test_estimate_episode_cost_positive():
    est = estimate_episode_cost(_ep(), load_config())
    assert est["total_usd"] > 0
    assert est["llm_usd"] > 0 and est["tts_usd"] > 0
    assert est["est_output_tokens"] > 0


def test_tts_cost_counts_chars():
    ep = _ep()
    ep.rounds = {"r1_prosecution": [Replica(round_id="r1_prosecution", side=Side.PROSECUTION,
                                            model_id="gpt-5.5", text="x" * 1000,
                                            used_text="x" * 1000)]}
    assert tts_cost(ep, load_config()) > 0


# --- I8 OTIO ---------------------------------------------------------------
def test_otio_valid_and_structured():
    ep = _ep_with_clips()
    td = build_timeline(ep, fps=30)
    data = json.loads(otio_generator.generate_otio(ep, td))
    assert data["OTIO_SCHEMA"] == "Timeline.1"
    tracks = data["tracks"]["children"]
    assert {t["name"] for t in tracks} == {"PROSECUTION", "DEFENSE"}
    clip_names = [c["name"] for t in tracks for c in t["children"] if c["OTIO_SCHEMA"] == "Clip.1"]
    assert "r1_prosecution" in clip_names and "r1_defense" in clip_names


def test_export_timeline_emits_otio_only_with_flag(tmp_path):
    res = export_timeline(_ep_with_clips(), tmp_path, emit_otio=True)
    assert res["otio"] and Path(res["otio"]).exists()
    res2 = export_timeline(_ep_with_clips(), tmp_path / "b", emit_otio=False)
    assert res2["otio"] is None


# --- I9 publish-pack -------------------------------------------------------
async def test_export_bundle_contains_expected(tmp_path):
    config = _cfg(tmp_path)
    ep = _ep("ep_bundle")
    await step_generate(ep, config, offline=True)
    step_tts(ep, config, offline=True)
    step_export(ep, config)
    step_script(ep, config)

    zip_path = export_bundle(ep, config)
    assert Path(zip_path).exists()
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert any(n.startswith("audio/") and n.endswith(".wav") for n in names)
    assert "script/episode_script.md" in names
    assert any(n.startswith("timeline/") for n in names)
    assert "HANDOFF.md" in names
