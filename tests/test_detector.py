"""Mandatory MVP tests for the Behaviour Detector (Module 2)."""
from __future__ import annotations

from modules.detector.behaviour_detector import detect
from modules.schemas import GenerationResult


def _res(content: str, reasoning: str | None = None, finish: str = "stop") -> GenerationResult:
    return GenerationResult(
        content=content, reasoning_content=reasoning,
        finish_reason=finish, model_id="deepseek-v4-pro",
    )


def test_content_filter_is_refused():
    flags = detect(_res("anything", finish="content_filter"), "deepseek-v4-pro", "r1_defense")
    assert any(f.flag_type == "REFUSED" for f in flags)
    assert flags[0].confidence == 1.0


def test_refusal_phrase_case_insensitive():
    flags = detect(_res("Sorry, That's Beyond My Current Scope."), "deepseek-v4-pro", "r1_defense")
    assert any(f.flag_type == "REFUSED" for f in flags)


def test_suppressed_short_reasoning_long_content():
    flags = detect(_res("x" * 300, reasoning="short"), "deepseek-v4-pro", "r1_defense")
    assert any(f.flag_type == "SUPPRESSED" for f in flags)


def test_empty_content_is_refused():
    flags = detect(_res("", reasoning=None), "deepseek-v4-pro", "r1_defense")
    assert any(f.flag_type == "REFUSED" for f in flags)


def test_evasive_hedging_multiple_hits():
    txt = ("It depends. On the other hand, both sides have merit. "
           "That said, it's complicated.")
    flags = detect(_res(txt, reasoning="a" * 100), "gpt-5.5", "r1_prosecution", is_deepseek=False)
    assert any(f.flag_type == "EVASIVE" for f in flags)


def test_weak_single_hedge():
    txt = "Arguably the economy declined sharply and the union could not hold."
    flags = detect(_res(txt), "gpt-5.5", "r1_prosecution", is_deepseek=False)
    assert any(f.flag_type == "WEAK" for f in flags)
    assert not any(f.flag_type == "EVASIVE" for f in flags)


def test_clean_answer_no_flags():
    txt = ("The command economy collapsed under its own contradictions. "
           "Output fell 40 percent in a single year.")
    flags = detect(_res(txt, reasoning="a" * 100), "gpt-5.5", "r1_prosecution", is_deepseek=False)
    assert flags == []


def test_non_deepseek_skips_censor_rules():
    # Short reasoning + long content would be SUPPRESSED for DeepSeek, but not here.
    flags = detect(_res("y" * 300, reasoning="short"), "gpt-5.5", "r1_prosecution", is_deepseek=False)
    assert not any(f.flag_type == "SUPPRESSED" for f in flags)
