"""Topic approval checklist (Block J.2).

The authoritative rule lives in modules.schemas.TopicChecklist's model_validator;
this module exposes the same rule as a standalone helper for the UI to preview
the auto-recommendation before constructing the full object.
"""
from __future__ import annotations

from modules.schemas import TopicChecklist


def evaluate(
    *,
    has_evidence_both_sides: bool,
    is_debatable: bool,
    deepseek_risk: str,
    monetization_risk: str,
    reach_vs_safety: int,
    freshness_vs_evergreen: int,
) -> str:
    """Return 'approved' | 'warning' | 'rejected' for the given criteria."""
    checklist = TopicChecklist(
        has_evidence_both_sides=has_evidence_both_sides,
        is_debatable=is_debatable,
        deepseek_risk=deepseek_risk,  # type: ignore[arg-type]
        monetization_risk=monetization_risk,  # type: ignore[arg-type]
        reach_vs_safety=reach_vs_safety,
        freshness_vs_evergreen=freshness_vs_evergreen,
    )
    return checklist.auto_status
