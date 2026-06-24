"""Tests for the remaining deterministic core (variability, drift, leaderboard,
checklist, metadata, Oxford delta)."""
from __future__ import annotations

from uuid import uuid4

from modules.leaderboard.engine import EpisodeOutcome, recompute
from modules.metadata.generator import DISCLAIMER, generate_metadata
from modules.quickfire.variability import variability_score
from modules.schemas import Episode, OxfordDelta, Side
from modules.topics.checklist import evaluate
from modules.translation.gemini_corrector import correct, detect_drift


# ---- variability ---------------------------------------------------------- #
def test_variability_identical_is_low():
    assert variability_score("The economy collapsed in 1991.", "The economy collapsed in 1991.") < 0.1


def test_variability_opposite_is_higher():
    s = variability_score("Yes, the collapse was inevitable and total.",
                          "No, reform was viable and avoidable.")
    assert s > 0.5


# ---- translation drift ---------------------------------------------------- #
def test_drift_on_changed_number():
    drift, score = detect_drift("GDP fell 40 percent in 1991.",
                                "GDP fell 4 percent in 1999.")
    assert drift is True
    assert score > 0


def test_no_drift_on_grammar_only_fix():
    drift, _ = detect_drift("economy collapse was inevitable because central planning failed",
                            "The economy collapse was inevitable because central planning failed.")
    assert drift is False


def test_correct_reverts_on_drift():
    res = correct(
        "GDP fell 40 percent in 1991.",
        needs_translation=True,
        corrector_fn=lambda sysp, txt: "GDP soared 90 percent in 2010.",
    )
    assert res.drift_detected is True
    assert res.used_text == res.original_text
    assert res.correction_applied is False


def test_correct_noop_when_not_needed():
    res = correct("anything", needs_translation=False)
    assert res.correction_applied is False
    assert res.used_text == "anything"


# ---- checklist ------------------------------------------------------------ #
def test_checklist_rejected_on_guaranteed_refused():
    assert evaluate(has_evidence_both_sides=True, is_debatable=True,
                    deepseek_risk="guaranteed_refused", monetization_risk="green",
                    reach_vs_safety=3, freshness_vs_evergreen=3) == "rejected"


def test_checklist_approved_path():
    assert evaluate(has_evidence_both_sides=True, is_debatable=True,
                    deepseek_risk="low", monetization_risk="green",
                    reach_vs_safety=4, freshness_vs_evergreen=3) == "approved"


def test_checklist_warning_path():
    assert evaluate(has_evidence_both_sides=False, is_debatable=True,
                    deepseek_risk="high", monetization_risk="yellow",
                    reach_vs_safety=2, freshness_vs_evergreen=2) == "warning"


# ---- leaderboard ---------------------------------------------------------- #
def test_leaderboard_recompute_wins_and_streak():
    meta = {
        "gpt-5.5": {"display_name": "GPT-5.5", "season": 1, "is_deepseek": False},
        "deepseek-v4-pro": {"display_name": "DeepSeek V4", "season": 1, "is_deepseek": True},
    }
    outcomes = [
        EpisodeOutcome("e1", "gpt-5.5", "deepseek-v4-pro", "gpt-5.5",
                       sustained_by_model={"gpt-5.5": 2}, refused_by_model={"deepseek-v4-pro": 1}),
        EpisodeOutcome("e2", "gpt-5.5", "deepseek-v4-pro", "gpt-5.5",
                       sustained_by_model={"gpt-5.5": 1, "deepseek-v4-pro": 1}),
    ]
    entries = {e.model_id: e for e in recompute(outcomes, meta)}
    assert entries["gpt-5.5"].win_count == 2
    assert entries["gpt-5.5"].win_streak == 2
    assert entries["deepseek-v4-pro"].explicit_refusal_pct == 50.0  # 1 of 2 episodes
    assert entries["gpt-5.5"].explicit_refusal_pct is None          # n/a for non-DeepSeek


# ---- oxford --------------------------------------------------------------- #
def test_oxford_winner_and_quorum():
    d = OxfordDelta(episode_id=uuid4(), agree_before=30, agree_after=55,
                    disagree_before=40, disagree_after=45, votes_before=50, votes_after=60)
    assert d.winner_side == Side.PROSECUTION  # +25 vs +5
    nq = OxfordDelta(episode_id=uuid4(), agree_before=30, agree_after=55,
                     disagree_before=40, disagree_after=45, votes_before=10, votes_after=60)
    assert nq.no_quorum is True
    assert nq.winner_side is None


# ---- metadata ------------------------------------------------------------- #
def test_metadata_title_and_disclaimer():
    ep = Episode(thesis="The dissolution of the USSR was inevitable.", slug="ep001",
                 prosecution_model_id="gpt-5.5", defense_model_id="deepseek-v4-pro")
    md = generate_metadata(ep, "GPT-5.5", "DeepSeek V4")
    assert len(md["title"]) <= 60
    assert DISCLAIMER in md["description"]
    assert md["pre_poll"].startswith("Before watching")
