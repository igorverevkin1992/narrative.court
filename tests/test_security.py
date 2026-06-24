"""Security / hardening tests for the audit remediation:
slug validation (path traversal), FCPXML escaping + control-char stripping,
markers_md sanitization, and atomic episode autosave."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from modules.config import load_config
from modules.episodes.manager import episode_dir, load_episode, save_episode
from modules.schemas import Episode, TimelineClip, TimelineData, TimelineMarker
from modules.timeline import edl_generator, fcpxml_generator
from modules.timeline.exporter import validate_fcpxml


def _episode(slug: str = "ep_sec") -> Episode:
    return Episode(thesis="t", slug=slug,
                   prosecution_model_id="gpt-5.5", defense_model_id="deepseek-v4-pro")


def test_slug_rejects_path_traversal_and_unsafe():
    for bad in ["../../etc", "ep/../x", "Ep Caps", "ep.dot", "ep-dash", "", "a" * 65, "/abs", "ep\\win"]:
        with pytest.raises(ValidationError):
            _episode(bad)


def test_slug_accepts_valid():
    for ok in ["ep001_dissolution_ussr", "ep_lb_test", "a", "x0"]:
        assert _episode(ok).slug == ok


def test_fcpxml_escapes_and_strips_control_chars():
    ep = _episode()
    clip = TimelineClip(clip_id='r1<&">\x07evil', track="PROSECUTION", lane=-1,
                        audio_path="/tmp/a.wav", start_frames=0, duration_frames=30)
    marker = TimelineMarker(marker_type='OBJ<&\x00', frame=0, track="SFX_MARKERS", note="x")
    td = TimelineData(fps=30, sample_rate=44100, clips=[clip], markers=[marker], total_frames=30)

    xml = fcpxml_generator.generate_fcpxml(ep, td)
    assert validate_fcpxml(xml) is True          # escaping kept it well-formed
    assert "\x00" not in xml and "\x07" not in xml  # control chars stripped
    assert "r1<" not in xml and "OBJ<" not in xml   # hostile '<' was escaped


def test_markers_md_sanitizes_newlines_and_pipes():
    ep = _episode()
    clip = TimelineClip(clip_id="r1_prosecution", track="PROSECUTION", lane=-1,
                        audio_path="/tmp/a.wav", start_frames=0, duration_frames=30)
    marker = TimelineMarker(marker_type="WEAK", frame=0, track="SFX_MARKERS",
                            note="line1\nline2 | pipe")
    td = TimelineData(clips=[clip], markers=[marker], total_frames=30)

    md = edl_generator.generate_markers_md(ep, td)
    rows = [ln for ln in md.splitlines() if ln.startswith("| ") and "WEAK" in ln]
    assert len(rows) == 1                       # note stayed a single table row
    assert "line1 line2 / pipe" in rows[0]       # newline collapsed, pipe neutralised


def test_save_episode_is_atomic_and_roundtrips(tmp_path):
    config = load_config()
    config._data["app"]["episodes_dir"] = str(tmp_path / "episodes")
    ep = _episode("ep_atomic")

    path = save_episode(ep, config)
    assert path.exists() and path.name == "episode.json"
    assert not (episode_dir(ep, config) / "episode.json.tmp").exists()  # no tmp leftover
    assert load_episode(path).slug == "ep_atomic"
