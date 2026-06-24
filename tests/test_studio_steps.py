"""Phase-2 tests: step-level orchestration, autosave, quickfire re-sync, and
objection suggestion -- all offline (MockAdapter + silent-WAV TTS)."""
from __future__ import annotations

from pathlib import Path

from modules.config import load_config
from modules.episodes import objections
from modules.episodes.manager import (
    episode_dir,
    list_saved_episodes,
    load_episode,
    n_clips,
    save_episode,
    step_export,
    step_generate,
    step_metadata,
    step_script,
    step_smoke_test,
    step_tts,
    sync_quickfire_selection,
)
from modules.schemas import Episode, EpisodeStatus


def _cfg(tmp_path):
    config = load_config()
    config._data["app"]["episodes_dir"] = str(tmp_path / "episodes")
    config._data["app"]["logs_dir"] = str(tmp_path / "logs")
    return config


def _episode() -> Episode:
    return Episode(
        thesis="The dissolution of the USSR was inevitable.",
        slug="ep900_steps",
        prosecution_model_id="gpt-5.5",
        defense_model_id="deepseek-v4-pro",
    )


async def test_steps_run_in_sequence_offline(tmp_path):
    config = _cfg(tmp_path)
    episode = _episode()

    smoke = await step_smoke_test(episode, config, offline=True)
    assert smoke.passed is True
    assert episode.status == EpisodeStatus.SMOKE_TESTED

    await step_generate(episode, config, offline=True)
    assert episode.status == EpisodeStatus.GENERATED
    assert n_clips(episode) > 0

    durations = step_tts(episode, config, offline=True)
    assert episode.status == EpisodeStatus.TTS_DONE
    assert len(durations) == n_clips(episode)
    audio = list((episode_dir(episode, config) / "audio").glob("*.wav"))
    assert len(audio) == n_clips(episode)

    exp = step_export(episode, config)
    assert episode.status == EpisodeStatus.EXPORTED
    assert Path(exp["fcpxml"]).exists()
    assert Path(exp["edl"]).exists()

    scr = step_script(episode, config)
    assert Path(scr["script"]).exists()

    meta = step_metadata(episode, config)
    assert meta.get("title")


async def test_save_and_load_episode(tmp_path):
    config = _cfg(tmp_path)
    episode = _episode()
    await step_generate(episode, config, offline=True)

    path = save_episode(episode, config)
    assert path.exists()
    reloaded = load_episode(path)
    assert reloaded.slug == episode.slug
    assert reloaded.thesis == episode.thesis
    assert n_clips(reloaded) == n_clips(episode)

    drafts = list_saved_episodes(config)
    assert any(d.slug == episode.slug for d in drafts)


async def test_sync_quickfire_selection_rebuilds_r2(tmp_path):
    config = _cfg(tmp_path)
    episode = _episode()
    await step_generate(episode, config, offline=True)

    # Force exactly 3 selected exchanges and re-sync.
    for i, ex in enumerate(episode.quickfire):
        ex.recommended = i < 3
    count = sync_quickfire_selection(episode)
    assert count == 3
    r2_pros = [k for k in episode.rounds if k.startswith("r2_q") and k.endswith("prosecution")]
    r2_def = [k for k in episode.rounds if k.startswith("r2_q") and k.endswith("defense")]
    assert len(r2_pros) == 3 and len(r2_def) == 3


async def test_quickfire_change_after_tts_then_export_tops_up_audio(tmp_path):
    """Operator re-selects quickfire after TTS (Studio 8 after 7); export must
    re-sync r2 and top up the missing audio so every clip has real duration."""
    config = _cfg(tmp_path)
    episode = _episode()
    await step_generate(episode, config, offline=True)
    step_tts(episode, config, offline=True)  # synth auto-selected set

    # Re-select the 4 lowest-scored exchanges (a different set than auto).
    for i, ex in enumerate(sorted(episode.quickfire, key=lambda x: x.variability_score)):
        ex.recommended = i < 4
    assert sync_quickfire_selection(episode) == 4

    # Export flow (as the UI does): resumable top-up TTS, then build timeline.
    step_tts(episode, config, offline=True)
    exp = step_export(episode, config)
    assert Path(exp["fcpxml"]).exists()

    r2_clips = [c for c in episode.timeline_data.clips if c.clip_id.startswith("r2_q")]
    assert len(r2_clips) == 8  # 4 questions x 2 sides
    assert all(c.duration_frames > 0 for c in r2_clips)


async def test_suggest_claims_returns_factual_objections(tmp_path):
    config = _cfg(tmp_path)
    episode = _episode()
    await step_generate(episode, config, offline=True)

    claims = objections.suggest_claims(episode)
    # Mock long-form replies carry a dated, quantified clause, so we expect real
    # candidates, all pending, all factual, and never from the short quickfire.
    assert len(claims) >= 1
    assert len(claims) <= 8
    assert all(o.ruling == "pending" for o in claims)
    assert all(not o.round_id.startswith("r2_q") for o in claims)
    assert all(any(ch.isdigit() for ch in o.claim) for o in claims)
