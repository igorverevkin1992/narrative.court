"""Topic assistant (I10): draft a full Topic pack from a bare thesis.

Asks an LLM (via the Orchestrator) for thesis variants, quickfire questions,
factual anchors, a recommended model pair and risk ratings. The LLM call goes
through the same offline path as everything else, so when no key is available
(or the mock is used) it falls back to a deterministic skeleton -- the operator
still gets a usable, editable Topic.
"""
from __future__ import annotations

import json
import re

from modules.config import Config
from modules.llm.orchestrator import Orchestrator
from modules.schemas import QuickfireQuestion, Topic, TopicChecklist, TopicStatus

_SYSTEM = (
    "You are a debate producer. Given a motion, return STRICT JSON with keys: "
    "thesis_variants (array of 3 rephrasings), quickfire (array of 8 short "
    "either/or questions), factual_anchors (array of 5 verifiable facts), "
    "recommended_pair (array [prosecution_model_id, defense_model_id]), "
    "category (one of safe|optimal|hot), monetization_risk (green|yellow|red), "
    "deepseek_risk (low|medium|high|guaranteed_refused). Return ONLY the JSON."
)

_FALLBACK_QF = [
    "Was the outcome inevitable or contingent?",
    "Did economic or political factors matter more?",
    "Was there a viable alternative path?",
    "Did leadership change the outcome?",
    "Was external pressure decisive?",
    "Is the consensus view correct?",
    "Did institutions or individuals drive events?",
    "Is the motion a matter of fact or judgment?",
]


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def _default_pair(config: Config) -> tuple[str, str]:
    s1 = config.models_by_season(1)
    if len(s1) >= 2:
        return (s1[0]["id"], s1[1]["id"])
    return ("gpt-5.5", "deepseek-v4-pro")


def _fallback_variants(thesis: str) -> list[str]:
    base = thesis.rstrip(".")
    return [thesis, f"Critically evaluate whether {base[0].lower() + base[1:]}.",
            f"From an evidence-first standpoint, {base[0].lower() + base[1:]}."]


def draft_topic(thesis: str, config: Config, *, offline: bool = True,
                model_id: str | None = None) -> Topic:
    """Return a draft Topic for ``thesis`` (LLM-assisted, deterministic fallback)."""
    orch = Orchestrator(config, offline=offline)
    mid = model_id or config.get("translation_corrector", "model_id", default="gemini-3.1-pro")
    data: dict = {}
    try:
        res = orch.generate(mid, _SYSTEM, f"Motion: {thesis}", 0.4, 800)
        data = _parse_json(res.content)
    except Exception:
        data = {}

    variants = data.get("thesis_variants") or _fallback_variants(thesis)
    qf = data.get("quickfire") or _FALLBACK_QF
    anchors = data.get("factual_anchors") or []
    pair = data.get("recommended_pair") or list(_default_pair(config))

    category = data.get("category") if data.get("category") in ("safe", "optimal", "hot") else "optimal"
    mon = data.get("monetization_risk") if data.get("monetization_risk") in ("green", "yellow", "red") else "yellow"
    ds = (data.get("deepseek_risk")
          if data.get("deepseek_risk") in ("low", "medium", "high", "guaranteed_refused") else "medium")

    checklist = TopicChecklist(
        has_evidence_both_sides=True, is_debatable=True,
        deepseek_risk=ds, monetization_risk=mon,
        reach_vs_safety=3, freshness_vs_evergreen=3)

    return Topic(
        thesis=thesis,
        thesis_variants=[str(v) for v in variants][:3],
        category=category, monetization_risk=mon, deepseek_risk=ds,
        recommended_pair=(str(pair[0]), str(pair[1])),
        factual_anchors=[str(a) for a in anchors][:8],
        quickfire_bank=[QuickfireQuestion(id=f"q{i + 1:02d}", text=str(q))
                        for i, q in enumerate(qf[:8])],
        checklist=checklist, status=TopicStatus.DRAFT,
        notes="drafted by topic assistant (I10)")
