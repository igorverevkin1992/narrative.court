"""Episode diagnostics for edge cases (Block O).

Surfaces model misbehaviour the operator must see before publishing: REFUSED /
SUPPRESSED flags raised in the substantive rounds (Block O.1). Quickfire (r2_*)
is excluded -- objections and verdicts hinge on the main arguments.
"""
from __future__ import annotations

from modules.schemas import Episode

_MAIN_PREFIXES = ("r1_", "r3_", "r4_")


def refused_or_suppressed_rounds(episode: Episode) -> list[dict]:
    """Return REFUSED/SUPPRESSED occurrences in r1/r3/r4.

    Each item: {"round_id", "model_id", "flag_type", "evidence"}.
    """
    out: list[dict] = []
    for rid in sorted(episode.rounds):
        if not rid.startswith(_MAIN_PREFIXES):
            continue
        for rep in episode.rounds[rid]:
            for fl in rep.flags:
                if fl.flag_type in ("REFUSED", "SUPPRESSED"):
                    out.append({
                        "round_id": rid,
                        "model_id": fl.model_id,
                        "flag_type": fl.flag_type,
                        "evidence": fl.evidence,
                    })
    return out
