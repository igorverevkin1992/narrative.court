"""Quickfire variability scorer (Block E.1) -- lexical heuristics, no API.

Chosen over embeddings/LLM-judge because it is $0, deterministic, and offline.
Composite of Jaccard distance, length delta, antonym-pair presence, and numeric
divergence between the two models' answers to the same question.
"""
from __future__ import annotations

import re

ANTONYM_PAIRS: list[tuple[str, str]] = [
    ("yes", "no"), ("increase", "decrease"), ("valid", "invalid"),
    ("legal", "illegal"), ("true", "false"), ("success", "failure"),
    ("rise", "fall"), ("support", "oppose"), ("inevitable", "avoidable"),
    ("agree", "disagree"), ("benefit", "harm"), ("strengthen", "weaken"),
    ("rose", "fell"), ("more", "less"), ("win", "lose"),
]

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "it", "its", "as", "that", "this",
    "by", "at", "from", "into", "about", "would", "could", "should", "their",
    "they", "we", "i", "you", "he", "she", "not", "no", "yes",
}


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS]


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def _jaccard_distance(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return 1.0 - (inter / union if union else 0.0)


def _antonym_score(words_a: set[str], words_b: set[str]) -> float:
    found = 0
    for w1, w2 in ANTONYM_PAIRS:
        if (w1 in words_a and w2 in words_b) or (w2 in words_a and w1 in words_b):
            found += 1
    return min(1.0, found * 0.5)


def _numeric_divergence(a: str, b: str) -> float:
    na, nb = _numbers(a), _numbers(b)
    if not na and not nb:
        return 0.0
    if na == nb:
        return 0.0
    if not na or not nb:
        return 0.5
    return 1.0


def variability_score(answer_a: str, answer_b: str) -> float:
    """Return a variability score in [0, 1] (higher = more opposed)."""
    tokens_a = _tokens(answer_a)
    tokens_b = _tokens(answer_b)
    set_a, set_b = set(tokens_a), set(tokens_b)

    jaccard = _jaccard_distance(set_a, set_b)
    len_delta = abs(len(tokens_a) - len(tokens_b)) / max(len(tokens_a), len(tokens_b), 1)
    antonym = _antonym_score(set_a, set_b)
    numeric = _numeric_divergence(answer_a, answer_b)

    score = 0.45 * jaccard + 0.15 * len_delta + 0.25 * antonym + 0.15 * numeric
    return round(min(1.0, score), 4)


def score_pair(answer_a, answer_b, *, method: str = "lexical", judge_fn=None) -> float:
    """Variability score in [0, 1] via the configured method (Block E.1 / I2).

    ``method="llm_judge"`` calls ``judge_fn(a, b) -> 0..1`` (e.g. a Gemini call
    that rates opposition); any error falls back to the deterministic lexical
    score, so offline and failures degrade gracefully.
    """
    if method == "llm_judge" and judge_fn is not None:
        try:
            return round(max(0.0, min(1.0, float(judge_fn(answer_a, answer_b)))), 4)
        except Exception:
            pass
    return variability_score(answer_a, answer_b)
