"""End-to-end integration: drive the full 10-step Studio flow offline, exactly
as the operator would, and assert every stage plus the edge-case guards."""
from __future__ import annotations

from pathlib import Path

from modules.config import load_config
from modules.episodes.diagnostics import refused_or_suppressed_rounds
from modules.episodes.manager import (
    step_export,
    step_generate,
    step_metadata,
    step_script,
    step_smoke_test,
    step_tts,
    save_episode,
    sync_quickfire_selection,
    tts_checkpoint_summary,
)
from modules.episodes.objections import suggest_claims
from modules.leaderboard.service import record_delta, recompute_and_persist
from modules.quickfire.manager import all_below_threshold
from modules.schemas import Episode, EpisodeStatus, OxfordDelta


async def test_full_studio_flow_offline(tmp_path):
    config = load_config()
    config._data["app"]["episodes_dir"] = str(tmp_path / "episodes")
    config._data["app"]["logs_dir"] = str(tmp_path / "logs")

    ep = Episode(thesis="The dissolution of the USSR was inevitable.", slug="ep_integration",
                 prosecution_model_id="gpt-5.5", defense_model_id="deepseek-v4-pro")

    # Step 3: smoke test
    smoke = await step_smoke_test(ep, config, offline=True)
    assert smoke.passed is True

    # Step 4: full generation + edge-case guards (clean mock)
    await step_generate(ep, config, offline=True)
    assert ep.status == EpisodeStatus.GENERATED
    assert refused_or_suppressed_rounds(ep) == []          # O.1: no refusals
    assert not all_below_threshold(ep.quickfire, 0.35)     # O.3: answers diverge

    # Step 6: objections (suggest + rule one sustained)
    claims = suggest_claims(ep)
    assert claims
    claims[0].ruling = "sustained"
    ep.objections = [c for c in claims if c.ruling in ("sustained", "overruled")]

    # Step 7: TTS (resumable)
    step_tts(ep, config, offline=True)
    assert tts_checkpoint_summary(ep, config)["remaining"] == 0   # O.2

    # Step 8: re-select quickfire (4 of 12) and re-sync r2
    for i, ex in enumerate(ep.quickfire):
        ex.recommended = i < 4
    assert sync_quickfire_selection(ep) == 4

    # Step 9: resilient export (tops up audio for the new selection) + script
    step_tts(ep, config, offline=True)
    exp = step_export(ep, config)
    assert exp["fcpxml_valid"] is True                     # O.4
    assert Path(exp["edl"]).exists() and Path(exp["markers_md"]).exists()
    assert ep.status == EpisodeStatus.EXPORTED
    scr = step_script(ep, config)
    assert Path(scr["script"]).exists()

    # Step 10: metadata
    meta = step_metadata(ep, config)
    assert meta.get("title")

    # Publish + leaderboard close-out
    record_delta(ep, OxfordDelta(episode_id=ep.id, agree_before=40, agree_after=60,
                                 disagree_before=30, disagree_after=35,
                                 votes_before=100, votes_after=100))
    save_episode(ep, config)
    entries = recompute_and_persist(config)
    assert any(e.win_count == 1 for e in entries)
