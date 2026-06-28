"""MVP-improvement tests (G1-G3).

G1: generation is resumable -- a mid-episode failure persists the finished rounds
    and a re-run fills only the gaps (no re-billing of completed work).
G3: a full-episode rough-cut preview.wav concatenates clips in broadcast order.
G2: a live run is gated by an optional per-episode spend cap.
"""
from __future__ import annotations

import pytest

from pathlib import Path

from modules.config import load_config
from modules.economics import within_budget
from modules.episodes.manager import (
    episode_dir,
    generation_progress,
    load_episode,
    n_clips,
    save_episode,
    step_generate,
    step_tts,
)
from modules.generator.episode_generator import EpisodeGenerator
from modules.llm.orchestrator import Orchestrator
from modules.preview import build_preview
from modules.schemas import Episode
from modules.timeline.timecode_calculator import _ordered_round_ids, audio_duration_sec


def _cfg(tmp_path):
    config = load_config()
    config._data["app"]["episodes_dir"] = str(tmp_path / "episodes")
    config._data["app"]["logs_dir"] = str(tmp_path / "logs")
    return config


def _ep(slug: str) -> Episode:
    return Episode(thesis="The dissolution of the USSR was inevitable.", slug=slug,
                   prosecution_model_id="gpt-5.5", defense_model_id="deepseek-v4-pro")


class _CountingGen(EpisodeGenerator):
    """Records generate calls per round_id and can inject one failure (G1)."""

    def __init__(self, *a, fail_round: str | None = None, **kw):
        super().__init__(*a, **kw)
        self.fail_round = fail_round
        self.calls: dict[str, int] = {}
        self._failed = False

    async def _gen_replica(self, episode, model_id, side, round_id, *a, **kw):
        self.calls[round_id] = self.calls.get(round_id, 0) + 1
        if round_id == self.fail_round and not self._failed:
            self._failed = True
            raise RuntimeError(f"injected failure on {round_id}")
        return await super()._gen_replica(episode, model_id, side, round_id, *a, **kw)


def _gen(config, ep, **kw) -> _CountingGen:
    return _CountingGen(config, Orchestrator(config, offline=True),
                        on_checkpoint=lambda e: save_episode(e, config), **kw)


# --- G1: resumable generation ---------------------------------------------
async def test_g1_failure_persists_completed_rounds(tmp_path):
    config = _cfg(tmp_path)
    ep = _ep("ep_g1_fail")
    gen = _gen(config, ep, fail_round="r3_p2_defense")

    with pytest.raises(RuntimeError, match="injected failure"):
        await gen.generate_rounds(ep)

    # The checkpoint must have written everything completed before the failure.
    path = episode_dir(ep, config) / "episode.json"
    assert path.exists()
    saved = load_episode(path)
    assert saved.rounds.get("r1_prosecution") and saved.rounds.get("r1_defense")
    assert saved.quickfire                                   # quickfire phase done
    assert saved.rounds.get("r3_p1_defense")                 # earlier R3 sub-round done
    assert saved.rounds.get("r3_p2_prosecution")             # partial-batch success survived
    assert "r3_p2_defense" not in saved.rounds               # the failed call did not persist
    assert "r4_prosecution" not in saved.rounds              # never reached R4
    prog = generation_progress(saved)
    assert 0 < prog["done"] < prog["expected"] and not prog["complete"]


async def test_g1_resume_fills_gaps_without_regenerating(tmp_path):
    config = _cfg(tmp_path)
    ep = _ep("ep_g1_resume")
    g1 = _gen(config, ep, fail_round="r3_p2_defense")
    with pytest.raises(RuntimeError):
        await g1.generate_rounds(ep)

    # Resume from disk with a fresh generator that counts every call.
    ep2 = load_episode(episode_dir(ep, config) / "episode.json")
    g2 = _gen(config, ep2)              # no injected failure
    await g2.generate_rounds(ep2)

    # Completed work must NOT be regenerated...
    for done_round in ("r1_prosecution", "r1_defense", "r3_p1_defense", "r3_p2_prosecution"):
        assert done_round not in g2.calls, f"{done_round} was wastefully regenerated"
    assert "qf_pros" not in g2.calls and "qf_def" not in g2.calls   # quickfire skipped
    # ...only the gaps are filled.
    assert "r3_p2_defense" in g2.calls and "r4_prosecution" in g2.calls
    assert generation_progress(ep2)["complete"] is True


async def test_g1_rerun_complete_episode_is_noop(tmp_path):
    config = _cfg(tmp_path)
    ep = _ep("ep_g1_noop")
    g1 = _gen(config, ep)
    await g1.generate_rounds(ep)
    assert generation_progress(ep)["complete"] is True

    g2 = _gen(config, ep)
    await g2.generate_rounds(ep)
    assert g2.calls == {}              # nothing left to generate


# --- G3: full-episode rough-cut preview ------------------------------------
async def test_g3_preview_duration_and_order(tmp_path):
    config = _cfg(tmp_path)
    ep = _ep("ep_g3")
    await step_generate(ep, config, offline=True)
    step_tts(ep, config, offline=True)

    gap = 0.2
    res = build_preview(ep, config, gap_sec=gap)
    assert Path(res["path"]).exists()
    assert res["clips"] == n_clips(ep) and not res["missing"]

    order = _ordered_round_ids(ep.rounds)
    audio_dir = episode_dir(ep, config) / "audio"
    clip_total = sum(audio_duration_sec(audio_dir / f"{rid}.wav") for rid in order)
    expected = clip_total + gap * (len(order) - 1)
    assert abs(res["duration_sec"] - expected) < 0.2
    # The concatenation must be at least as long as any single clip.
    assert res["duration_sec"] >= clip_total - 0.01


async def test_g3_preview_requires_audio(tmp_path):
    config = _cfg(tmp_path)
    ep = _ep("ep_g3_noaudio")
    await step_generate(ep, config, offline=True)   # rounds exist, but no TTS yet
    with pytest.raises(ValueError, match="run TTS"):
        build_preview(ep, config)


# --- G2: live-spend cap ----------------------------------------------------
def test_g2_within_budget_cap_logic(tmp_path):
    config = _cfg(tmp_path)
    ep = _ep("ep_g2")

    config._data["pricing"]["max_episode_usd"] = None      # no cap -> always ok
    ok, est, cap = within_budget(ep, config)
    assert ok and cap is None and est > 0

    config._data["pricing"]["max_episode_usd"] = est + 1.0  # cap above estimate
    ok2, est2, cap2 = within_budget(ep, config)
    assert ok2 and cap2 == est + 1.0 and est2 == est

    config._data["pricing"]["max_episode_usd"] = est / 2.0  # cap below estimate
    ok3, est3, _ = within_budget(ep, config)
    assert not ok3 and est3 == est
