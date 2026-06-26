"""Quickfire Manager (Module 4).

Scores 12 exchanges, recommends the top N (default 10), and trims answers to a
word budget so each reply fits the 15-second timer (Block E.2, primary lever is
generation max_tokens; this is the post-trim safety net).
"""
from __future__ import annotations

from modules.quickfire.variability import score_pair, variability_score
from modules.schemas import QuickfireExchange

WORD_BUDGET = 42  # ~15 s of speech at ~160 wpm


def trim_to_words(text: str, max_words: int = WORD_BUDGET) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]).rstrip(",;:") + "..."


def score_and_select(
    exchanges: list[QuickfireExchange],
    select: int = 10,
    threshold: float = 0.35,
    *,
    method: str = "lexical",
    judge_fn=None,
) -> list[QuickfireExchange]:
    """Score every exchange, mark the top ``select`` as recommended.

    Returns the same list (mutated) sorted by descending variability score.
    ``method``/``judge_fn`` select the scoring backend (lexical | llm_judge, I2).
    """
    for ex in exchanges:
        ex.variability_score = score_pair(
            ex.prosecution_answer, ex.defense_answer, method=method, judge_fn=judge_fn)

    ranked = sorted(exchanges, key=lambda e: e.variability_score, reverse=True)
    for i, ex in enumerate(ranked):
        ex.recommended = i < select and ex.variability_score >= threshold
    return ranked


def all_below_threshold(exchanges: list[QuickfireExchange], threshold: float = 0.35) -> bool:
    """Block O.3 helper: every exchange is too similar."""
    return bool(exchanges) and all(e.variability_score < threshold for e in exchanges)
