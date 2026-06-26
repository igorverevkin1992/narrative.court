"""Functional-improvement tests: pluggable variability (I2), multi-variant
generation + selection (I1), topic assistant (I10), batch episodes (I7)."""
from __future__ import annotations

from modules.config import load_config
from modules.episodes.manager import (
    generate_variants,
    regenerate_replica,
    run_batch_episodes,
    select_variant,
    step_generate,
)
from modules.quickfire.manager import score_and_select
from modules.quickfire.variability import score_pair, variability_score
from modules.schemas import Episode, QuickfireExchange
from modules.topics.assistant import draft_topic


def _cfg(tmp_path):
    c = load_config()
    c._data["app"]["episodes_dir"] = str(tmp_path / "episodes")
    c._data["app"]["logs_dir"] = str(tmp_path / "logs")
    return c


def _ep(slug: str) -> Episode:
    return Episode(thesis="The dissolution of the USSR was inevitable.", slug=slug,
                   prosecution_model_id="gpt-5.5", defense_model_id="deepseek-v4-pro")


# --- I2 pluggable variability ----------------------------------------------
def test_score_pair_methods_and_fallback():
    a, b = "Yes inevitable", "No avoidable"
    assert score_pair(a, b, method="lexical") == variability_score(a, b)
    assert score_pair(a, b, method="llm_judge", judge_fn=lambda x, y: 0.9) == 0.9

    def boom(x, y):
        raise RuntimeError("judge down")
    assert score_pair(a, b, method="llm_judge", judge_fn=boom) == variability_score(a, b)


def test_score_and_select_honours_method():
    ex = [QuickfireExchange(question="q", prosecution_answer="Yes", defense_answer="No")]
    score_and_select(ex, select=1, threshold=0.0, method="llm_judge", judge_fn=lambda a, b: 1.0)
    assert ex[0].variability_score == 1.0 and ex[0].recommended


def test_drift_dispatcher_llm_judge():
    from modules.translation.gemini_corrector import correct
    res = correct("In 1991 the USSR dissolved.", needs_translation=True,
                  corrector_fn=lambda sysp, t: "In 1991 the USSR dissolved.",
                  threshold=0.70, drift_method="llm_judge", drift_judge_fn=lambda o, c: 0.9)
    assert res.drift_detected is True and res.used_text == res.original_text  # high drift -> revert

    res2 = correct("x", needs_translation=True, corrector_fn=lambda s, t: "x.",
                   threshold=0.70, drift_method="llm_judge", drift_judge_fn=lambda o, c: 0.0)
    assert res2.drift_detected is False and res2.used_text == "x."  # low drift -> keep


# --- I1 multi-variant + selection ------------------------------------------
async def test_generate_variants_and_select(tmp_path):
    config = _cfg(tmp_path)
    ep = _ep("ep_var")
    await step_generate(ep, config, offline=True)

    variants = await generate_variants(ep, config, "r1_prosecution", n=3, offline=True)
    assert len(variants) == 3 and len(set(variants)) >= 2          # seeds diverge
    assert ep.rounds["r1_prosecution"][0].variants == variants

    select_variant(ep, "r1_prosecution", variants[1])
    rep = ep.rounds["r1_prosecution"][0]
    assert rep.text == variants[1] and rep.used_text == variants[1]


async def test_regenerate_replica_keeps_old_as_variant(tmp_path):
    config = _cfg(tmp_path)
    ep = _ep("ep_regen")
    await step_generate(ep, config, offline=True)
    old = ep.rounds["r1_defense"][0].text

    rep = await regenerate_replica(ep, config, "r1_defense", offline=True, seed=999)
    assert old in rep.variants
    assert ep.rounds["r1_defense"][0] is rep


# --- I10 topic assistant ----------------------------------------------------
def test_draft_topic_offline(tmp_path):
    config = _cfg(tmp_path)
    topic = draft_topic("The dissolution of the USSR was inevitable.", config, offline=True)
    assert len(topic.thesis_variants) == 3
    assert len(topic.quickfire_bank) == 8
    assert topic.recommended_pair[0] and topic.recommended_pair[1]
    assert topic.checklist.auto_status in ("approved", "warning", "rejected")


# --- I7 batch ---------------------------------------------------------------
async def test_run_batch_episodes(tmp_path):
    config = _cfg(tmp_path)
    results = await run_batch_episodes([_ep("ep_b1"), _ep("ep_b2")], config, offline=True)
    assert len(results) == 2
    assert all(r["ok"] for r in results)
    assert all(r["n_clips"] > 0 for r in results)


# --- I3 hedging LLM-judge ---------------------------------------------------
def test_i3_hedging_llm_judge():
    from modules.detector.behaviour_detector import detect_hedging
    from modules.schemas import GenerationResult
    res = GenerationResult(content="A confident, direct claim with no hedging.", model_id="x")

    evasive = detect_hedging(res, "x", "r1", judge_fn=lambda c: 0.8)
    assert evasive and evasive[0].flag_type == "EVASIVE"
    assert evasive[0].rule_triggered == "hedging_llm_judge"

    weak = detect_hedging(res, "x", "r1", judge_fn=lambda c: 0.4)
    assert weak and weak[0].flag_type == "WEAK"

    assert detect_hedging(res, "x", "r1", judge_fn=lambda c: 0.0) == []

    def boom(c):
        raise RuntimeError("judge down")
    assert detect_hedging(res, "x", "r1", judge_fn=boom) == []  # fallback: no keyword hits
