"""Objection candidate extraction (Block L.1.4, Studio step 6).

The host may only raise an objection against a *verifiable factual claim*, never
a value judgment (Production Bible: "OBJECTION ... only to verifiable factual
statements"). This module scans the substantive rounds (opening, cross-exam,
closing -- not the short quickfire) and surfaces sentences that carry a factual
signal (a year, a number/percentage, or a multi-word proper noun) as *pending*
ObjectionEvents. The operator then rules each one Sustained / Overruled in the UI.

Deterministic and API-free so it is unit-testable offline.
"""
from __future__ import annotations

import re

from modules.schemas import Episode, ObjectionEvent

# Rounds that carry argued factual claims worth objecting to.
_FACTUAL_ROUND_PREFIXES = ("r1_", "r3_", "r4_")

_YEAR = re.compile(r"\b(?:1[5-9]\d{2}|20\d{2})\b")
_PERCENT = re.compile(r"\b\d+(?:\.\d+)?\s?%")
_NUMBER = re.compile(r"\b\d{2,}(?:\.\d+)?\b")
_PROPER_CLUSTER = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _is_factual(sentence: str) -> bool:
    return bool(
        _YEAR.search(sentence)
        or _PERCENT.search(sentence)
        or _NUMBER.search(sentence)
        or _PROPER_CLUSTER.search(sentence)
    )


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]


def suggest_claims(
    episode: Episode, *, max_per_replica: int = 2, max_total: int = 8
) -> list[ObjectionEvent]:
    """Return pending ObjectionEvents for verifiable factual claims in the episode.

    Walks substantive rounds in speaking order, keeps factual sentences (deduped),
    caps per replica and overall to keep the operator's review list manageable.
    """
    out: list[ObjectionEvent] = []
    seen: set[str] = set()
    for rid in sorted(episode.rounds.keys()):
        if not rid.startswith(_FACTUAL_ROUND_PREFIXES):
            continue
        for replica in episode.rounds[rid]:
            picked = 0
            for sentence in split_sentences(replica.text):
                if picked >= max_per_replica or len(out) >= max_total:
                    break
                key = sentence.lower()
                if key in seen or len(sentence) < 12 or not _is_factual(sentence):
                    continue
                seen.add(key)
                out.append(ObjectionEvent(
                    round_id=replica.round_id, side=replica.side, claim=sentence,
                ))
                picked += 1
            if len(out) >= max_total:
                break
        if len(out) >= max_total:
            break
    return out
