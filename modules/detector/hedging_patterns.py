"""Hedging / evasion lexical patterns for western and general models (Block C.2).

A deterministic keyword/regex list is used instead of an LLM-judge: it is free,
reproducible, transparent, and auditable. >= 30 documented hedging patterns.
Counting policy lives in ``behaviour_detector.detect_hedging``.
"""
from __future__ import annotations

import re

# Phrases that signal the speaker is softening, balancing, or refusing to commit.
# Kept lowercase; matching is done against a lowercased, whitespace-normalized text.
HEDGING_PATTERNS: list[str] = [
    "however the other side",
    "however, the other side",
    "on the other hand",
    "it depends",
    "i should note",
    "i would note",
    "it's worth noting",
    "it is worth noting",
    "it's worth considering",
    "it is important to consider",
    "it's important to acknowledge",
    "it is important to acknowledge",
    "it's important to recognize",
    "while x is true",
    "while it is true",
    "while that is true",
    "both sides have merit",
    "there are valid points on both sides",
    "this is a nuanced question",
    "it's a nuanced",
    "a nuanced issue",
    "reasonable people can disagree",
    "to be fair to the other side",
    "in fairness to the opposing",
    "that said",
    "to some extent",
    "to a certain extent",
    "arguably",
    "one could argue the opposite",
    "the opposing view also",
    "it's not entirely clear",
    "it is not entirely clear",
    "there is no simple answer",
    "i can't take a definitive",
    "i cannot take a definitive",
    "it would be unfair to say",
    "of course, the other perspective",
    "admittedly, the other",
    "i must acknowledge the counterargument",
    "it's complicated",
    "it is complicated",
]

# Pre-compiled regexes (escaped literals). Word-ish boundaries kept loose because
# many patterns contain punctuation/contractions.
_COMPILED: list[tuple[str, re.Pattern[str]]] = [
    (p, re.compile(re.escape(p), re.IGNORECASE)) for p in HEDGING_PATTERNS
]


def find_hedges(text: str) -> list[str]:
    """Return the list of hedging phrases found in ``text`` (one entry per hit)."""
    if not text:
        return []
    normalized = re.sub(r"\s+", " ", text)
    hits: list[str] = []
    for phrase, rx in _COMPILED:
        if rx.search(normalized):
            hits.append(phrase)
    return hits
