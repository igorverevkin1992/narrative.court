"""Translation Layer (Module 5).

Grammar-only correction by Gemini for non-flagship-English S2 models, with
deterministic drift detection (Block F.2, keyword/entity/number overlap, no API)
and automatic revert to the original on drift.

The Gemini call itself is injected as ``corrector_fn`` so the drift logic is
testable offline and the real API call lives in the orchestrator.
"""
from __future__ import annotations

import re
from typing import Callable

from modules.schemas import TranslationResult

CORRECTOR_SYSTEM_PROMPT = (
    "You are a strict copy-editor. Fix ONLY grammar, syntax, spelling, and "
    "article/preposition usage in the user's English text. You MUST NOT: rephrase "
    "or reorder arguments; add, remove, or alter any fact, number, name, or claim; "
    "change the tone, stance, intensity, or hedging of the position; add commentary "
    "or meta-text. Return ONLY the corrected text. If it is already correct, return "
    "it verbatim. Preserve sentence count and argument order exactly."
)


def _key_terms(text: str) -> set[str]:
    """Numbers + capitalized tokens + significant lowercase nouns (>=5 chars)."""
    numbers = set(re.findall(r"\d+(?:\.\d+)?", text))
    capitals = set(re.findall(r"\b[A-Z][a-zA-Z]+\b", text))
    longish = set(w.lower() for w in re.findall(r"\b[a-zA-Z]{5,}\b", text))
    return numbers | {c.lower() for c in capitals} | longish


def detect_drift(original: str, corrected: str, threshold: float = 0.70) -> tuple[bool, float]:
    """Return (drift_detected, drift_score). drift_score = 1 - overlap."""
    keys_orig = _key_terms(original)
    if not keys_orig:
        return False, 0.0
    keys_corr = _key_terms(corrected)
    overlap = len(keys_corr & keys_orig) / len(keys_orig)
    drift_score = round(1.0 - overlap, 4)
    return (overlap < threshold), drift_score


def correct(
    original_text: str,
    *,
    needs_translation: bool,
    corrector_fn: Callable[[str, str], str] | None = None,
    threshold: float = 0.70,
    drift_method: str = "keyword_overlap",
    drift_judge_fn: Callable[[str, str], float] | None = None,
) -> TranslationResult:
    """Apply grammar correction with drift guard.

    ``corrector_fn(system_prompt, text) -> corrected_text`` performs the Gemini
    call. ``drift_method`` selects the drift backend (keyword_overlap | llm_judge,
    I2); ``drift_judge_fn(orig, corrected) -> 0..1`` is the LLM drift score. If
    ``needs_translation`` is False or no corrector is supplied, the original text
    is used unchanged.
    """
    if not needs_translation or corrector_fn is None:
        return TranslationResult(
            original_text=original_text, corrected_text=original_text,
            drift_detected=False, drift_score=None,
            used_text=original_text, correction_applied=False,
        )

    try:
        corrected = corrector_fn(CORRECTOR_SYSTEM_PROMPT, original_text)
    except Exception:
        corrected = ""

    if not corrected.strip():
        return TranslationResult(
            original_text=original_text, corrected_text=original_text,
            drift_detected=False, drift_score=None,
            used_text=original_text, correction_applied=False,
        )

    if drift_method == "llm_judge" and drift_judge_fn is not None:
        try:
            score = round(max(0.0, min(1.0, float(drift_judge_fn(original_text, corrected)))), 4)
            drift = score > (1.0 - threshold)
        except Exception:
            drift, score = detect_drift(original_text, corrected, threshold)
    else:
        drift, score = detect_drift(original_text, corrected, threshold)
    used = original_text if drift else corrected
    return TranslationResult(
        original_text=original_text, corrected_text=corrected,
        drift_detected=drift, drift_score=score,
        used_text=used, correction_applied=not drift,
    )
