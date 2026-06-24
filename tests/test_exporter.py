"""Mandatory MVP tests for the Timeline Exporter (Module 8)."""
from __future__ import annotations

import os

from modules.schemas import (
    BehaviourFlag,
    Episode,
    ObjectionEvent,
    Replica,
    Side,
)
from modules.timeline import edl_generator, fcpxml_generator
from modules.timeline.exporter import export_timeline, validate_fcpxml
from modules.timeline.timecode_calculator import build_timeline, frames_to_timecode


def _episode() -> Episode:
    ep = Episode(
        thesis="The dissolution of the USSR was inevitable.",
        slug="ep001_test",
        prosecution_model_id="gpt-5.5",
        defense_model_id="deepseek-v4-pro",
    )
    ep.rounds = {
        "r1_prosecution": [Replica(
            round_id="r1_prosecution", side=Side.PROSECUTION, model_id="gpt-5.5",
            text="opening", audio_path="audio/r1_prosecution.wav", duration_sec=8.0,
        )],
        "r1_defense": [Replica(
            round_id="r1_defense", side=Side.DEFENSE, model_id="deepseek-v4-pro",
            text="rebut", audio_path="audio/r1_defense.wav", duration_sec=10.0,
            flags=[BehaviourFlag(
                flag_type="SUPPRESSED", confidence=0.8, evidence="x",
                rule_triggered="rule3_empty_reasoning",
                model_id="deepseek-v4-pro", round_id="r1_defense",
            )],
        )],
        "r2_q01_prosecution": [Replica(
            round_id="r2_q01_prosecution", side=Side.PROSECUTION, model_id="gpt-5.5",
            text="quick", audio_path="audio/r2_q01_prosecution.wav", duration_sec=4.0,
        )],
    }
    ep.objections = [ObjectionEvent(
        round_id="r1_defense", side=Side.DEFENSE, claim="GDP fell 40% in 1991",
        ruling="sustained",
    )]
    return ep


def test_timecode_cursor_accumulation():
    td = build_timeline(_episode(), fps=30)
    starts = {c.clip_id: c.start_frames for c in td.clips}
    durs = {c.clip_id: c.duration_frames for c in td.clips}
    assert durs["r1_prosecution"] == 240
    assert durs["r1_defense"] == 300
    assert durs["r2_q01_prosecution"] == 120
    # prosecution speaks before defense within r1, then quickfire follows
    assert starts["r1_prosecution"] == 0
    assert starts["r1_defense"] == 240
    assert starts["r2_q01_prosecution"] == 540
    assert td.total_frames == 660


def test_frames_to_timecode():
    assert frames_to_timecode(0, 30) == "00:00:00:00"
    assert frames_to_timecode(540, 30) == "00:00:18:00"
    assert frames_to_timecode(31, 30) == "00:00:01:01"


def test_fcpxml_wellformed_and_structure():
    ep = _episode()
    td = build_timeline(ep, fps=30)
    xml = fcpxml_generator.generate_fcpxml(ep, td)
    assert validate_fcpxml(xml)
    from lxml import etree

    root = etree.fromstring(xml.encode("utf-8"))
    clips = root.findall(".//asset-clip")
    assert len(clips) == 3
    assert {c.get("lane") for c in clips} == {"-1", "-2"}
    assert len(root.findall(".//marker")) >= 1
    assert root.get("version") == "1.11"


def test_edl_and_markers():
    ep = _episode()
    td = build_timeline(ep, fps=30)
    edl = edl_generator.generate_edl(ep, td)
    assert edl.count("FROM CLIP NAME") == 3
    assert "TITLE: ep001_test" in edl
    md = edl_generator.generate_markers_md(ep, td)
    assert "OBJECTION_SUSTAINED" in md
    assert "ROUND_START_1" in md


def test_export_writes_files(tmp_path):
    ep = _episode()
    res = export_timeline(ep, tmp_path)
    assert os.path.exists(res["fcpxml"])
    assert os.path.exists(res["edl"])
    assert os.path.exists(res["markers_md"])
    assert ep.timeline_data is not None
    assert ep.timeline_data.total_frames == 660
