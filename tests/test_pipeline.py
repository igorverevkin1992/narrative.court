"""End-to-end offline pipeline test (Phase 1 MVP accept criterion).

Runs the whole episode pipeline through MockAdapter + offline TTS and asserts the
episode folder, audio, script, and timeline artifacts are produced.
"""
from __future__ import annotations

from pathlib import Path

from modules.config import load_config
from modules.episodes.manager import run_full_pipeline
from modules.schemas import Episode, EpisodeStatus


async def test_offline_pipeline_creates_episode_folder(tmp_path):
    config = load_config()
    # redirect outputs into the test's tmp dir
    config._data["app"]["episodes_dir"] = str(tmp_path / "episodes")
    config._data["app"]["logs_dir"] = str(tmp_path / "logs")

    episode = Episode(
        thesis="The dissolution of the USSR was inevitable.",
        slug="ep999_offline_test",
        prosecution_model_id="gpt-5.5",
        defense_model_id="deepseek-v4-pro",
    )
    res = await run_full_pipeline(episode, config, offline=True)

    assert episode.status == EpisodeStatus.EXPORTED
    assert Path(res["fcpxml"]).exists()
    assert Path(res["edl"]).exists()
    assert Path(res["markers_md"]).exists()
    assert Path(res["script"]).exists()
    assert res["n_clips"] > 0

    audio = list(Path(res["audio_dir"]).glob("*.wav"))
    assert len(audio) == res["n_clips"]
    # at least the four core round clips are present
    names = {p.name for p in audio}
    assert {"r1_prosecution.wav", "r1_defense.wav",
            "r4_prosecution.wav", "r4_defense.wav"} <= names


async def test_offline_pipeline_selects_quickfire(tmp_path):
    config = load_config()
    config._data["app"]["episodes_dir"] = str(tmp_path / "episodes")
    config._data["app"]["logs_dir"] = str(tmp_path / "logs")
    episode = Episode(
        thesis="NATO expansion caused the security crisis.",
        slug="ep998_qf_test",
        prosecution_model_id="gpt-5.5",
        defense_model_id="claude-opus-4.8",
    )
    await run_full_pipeline(episode, config, offline=True)
    assert any(ex.recommended for ex in episode.quickfire)
    assert episode.youtube_metadata.get("title")
