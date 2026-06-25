"""Edge-case tests (Block O.1-O.7): per-scenario helpers + resilient export."""
from __future__ import annotations

from pathlib import Path

from modules import config as config_mod
from modules.config import load_config, missing_keys
from modules.episodes.diagnostics import refused_or_suppressed_rounds
from modules.episodes.manager import (
    n_clips,
    reset_tts_checkpoint,
    save_episode,
    step_generate,
    step_tts,
    tts_checkpoint_summary,
)
from modules.leaderboard.service import episodes_pending_delta, record_delta
from modules.quickfire.manager import all_below_threshold
from modules.schemas import (
    BehaviourFlag,
    Episode,
    EpisodeStatus,
    OxfordDelta,
    QuickfireExchange,
    Replica,
    Side,
)
from modules.timeline import exporter, fcpxml_generator


def _ep(slug: str = "ep_o") -> Episode:
    return Episode(thesis="The dissolution of the USSR was inevitable.", slug=slug,
                   prosecution_model_id="gpt-5.5", defense_model_id="deepseek-v4-pro")


def _quorum_delta(ep: Episode) -> OxfordDelta:
    return OxfordDelta(episode_id=ep.id, agree_before=40, agree_after=60,
                       disagree_before=30, disagree_after=35, votes_before=100, votes_after=100)


def _cfg(tmp_path):
    config = load_config()
    config._data["app"]["episodes_dir"] = str(tmp_path / "episodes")
    config._data["app"]["logs_dir"] = str(tmp_path / "logs")
    return config


# --- O.1 -------------------------------------------------------------------
def test_o1_refused_or_suppressed_excludes_quickfire():
    ep = _ep("ep_o1")
    ep.rounds = {
        "r1_defense": [Replica(round_id="r1_defense", side=Side.DEFENSE,
                               model_id="deepseek-v4-pro", text="x",
                               flags=[BehaviourFlag(flag_type="REFUSED", confidence=1.0,
                                                    evidence="beyond scope", rule_triggered="rule2",
                                                    model_id="deepseek-v4-pro", round_id="r1_defense")])],
        "r2_q01_defense": [Replica(round_id="r2_q01_defense", side=Side.DEFENSE,
                                   model_id="deepseek-v4-pro", text="y",
                                   flags=[BehaviourFlag(flag_type="SUPPRESSED", confidence=0.8,
                                                        evidence="z", rule_triggered="rule3",
                                                        model_id="deepseek-v4-pro", round_id="r2_q01_defense")])],
    }
    res = refused_or_suppressed_rounds(ep)
    assert len(res) == 1
    assert res[0]["round_id"] == "r1_defense" and res[0]["flag_type"] == "REFUSED"


# --- O.2 -------------------------------------------------------------------
async def test_o2_tts_checkpoint_summary_and_reset(tmp_path):
    config = _cfg(tmp_path)
    ep = _ep("ep_o2")
    await step_generate(ep, config, offline=True)
    step_tts(ep, config, offline=True)

    s = tts_checkpoint_summary(ep, config)
    assert s["total"] == n_clips(ep) and s["done"] == s["total"] and s["remaining"] == 0
    assert reset_tts_checkpoint(ep, config) is True
    assert tts_checkpoint_summary(ep, config)["done"] == 0
    assert reset_tts_checkpoint(ep, config) is False  # already cleared


# --- O.3 -------------------------------------------------------------------
def test_o3_all_below_threshold():
    same = [QuickfireExchange(question="q", prosecution_answer="Yes inevitable",
                             defense_answer="Yes inevitable", variability_score=0.1)]
    diff = [QuickfireExchange(question="q", prosecution_answer="Yes",
                             defense_answer="No", variability_score=0.9)]
    assert all_below_threshold(same, 0.35) is True
    assert all_below_threshold(diff, 0.35) is False


# --- O.4 -------------------------------------------------------------------
def _ep_with_audio() -> Episode:
    ep = _ep("ep_o4")
    ep.rounds = {
        "r1_prosecution": [Replica(round_id="r1_prosecution", side=Side.PROSECUTION,
                                   model_id="gpt-5.5", text="opening",
                                   audio_path="audio/r1_prosecution.wav", duration_sec=8.0)],
        "r1_defense": [Replica(round_id="r1_defense", side=Side.DEFENSE,
                               model_id="deepseek-v4-pro", text="rebut",
                               audio_path="audio/r1_defense.wav", duration_sec=10.0)],
    }
    return ep


def test_o4_export_resilient_on_invalid_fcpxml(tmp_path, monkeypatch):
    monkeypatch.setattr(fcpxml_generator, "generate_fcpxml", lambda e, t, **k: "<broken <xml")
    res = exporter.export_timeline(_ep_with_audio(), tmp_path)
    assert res["fcpxml_valid"] is False           # flagged, not raised
    assert Path(res["edl"]).exists()               # fallback still written
    assert Path(res["markers_md"]).exists()
    assert Path(res["fcpxml"]).exists()            # kept for inspection


def test_o4_export_happy_path_is_valid(tmp_path):
    res = exporter.export_timeline(_ep_with_audio(), tmp_path)
    assert res["fcpxml_valid"] is True
    assert Path(res["fcpxml"]).exists() and Path(res["edl"]).exists()


# --- O.5 -------------------------------------------------------------------
def test_o5_episodes_pending_delta(tmp_path):
    config = _cfg(tmp_path)
    pend = _ep("ep_pending")
    pend.status = EpisodeStatus.EXPORTED
    save_episode(pend, config)
    done = _ep("ep_done")
    done.status = EpisodeStatus.PUBLISHED
    record_delta(done, _quorum_delta(done))
    save_episode(done, config)

    slugs = {e.slug for e in episodes_pending_delta(config)}
    assert "ep_pending" in slugs and "ep_done" not in slugs


# --- O.7 -------------------------------------------------------------------
def test_o7_missing_keys_lists_unset(monkeypatch):
    monkeypatch.setattr(config_mod, "get_secret", lambda name: None)
    miss = missing_keys(load_config())
    assert "ELEVENLABS_API_KEY" in miss
    assert all(isinstance(x, str) for x in miss)
    assert len(miss) >= 8
