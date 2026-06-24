"""Leaderboard Engine (Module 9) -- 5 metrics per model (Block I.2).

Pure recompute logic over in-memory episode records, kept side-effect-free for
testability. The DB wiring (reading flags/objections, persisting aggregates)
calls these helpers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from modules.schemas import LeaderboardEntry, OxfordDelta, Side


@dataclass
class EpisodeOutcome:
    """Per-episode facts needed to recompute the leaderboard."""
    episode_id: str
    prosecution_model_id: str
    defense_model_id: str
    winner_model_id: str | None          # None => no_quorum / no win
    sustained_by_model: dict[str, int] = field(default_factory=dict)
    overruled_by_model: dict[str, int] = field(default_factory=dict)
    refused_by_model: dict[str, int] = field(default_factory=dict)


def winner_model_id(delta: OxfordDelta, prosecution_id: str, defense_id: str) -> str | None:
    side = delta.winner_side
    if side is None:
        return None
    return prosecution_id if side == Side.PROSECUTION else defense_id


def recompute(outcomes: list[EpisodeOutcome], model_meta: dict[str, dict]) -> list[LeaderboardEntry]:
    """Recompute all five metrics for every model from the episode history.

    ``model_meta[model_id]`` -> {"display_name": str, "season": int, "is_deepseek": bool}
    """
    agg: dict[str, dict] = {
        mid: {
            "wins": 0, "sustained": 0, "overruled": 0, "refused_episodes": 0,
            "episodes": 0, "sustained_total": 0, "streak": 0,
        }
        for mid in model_meta
    }

    for o in outcomes:
        for mid in (o.prosecution_model_id, o.defense_model_id):
            if mid not in agg:
                continue
            agg[mid]["episodes"] += 1
            agg[mid]["sustained"] += o.sustained_by_model.get(mid, 0)
            agg[mid]["overruled"] += o.overruled_by_model.get(mid, 0)
            agg[mid]["sustained_total"] += o.sustained_by_model.get(mid, 0)
            if o.refused_by_model.get(mid, 0) > 0:
                agg[mid]["refused_episodes"] += 1
        # win + streak handling
        for mid in (o.prosecution_model_id, o.defense_model_id):
            if mid not in agg:
                continue
            if o.winner_model_id == mid:
                agg[mid]["wins"] += 1
                agg[mid]["streak"] += 1
            elif o.winner_model_id is not None:
                agg[mid]["streak"] = 0  # reset only on an actual loss (quorum present)

    entries: list[LeaderboardEntry] = []
    for mid, meta in model_meta.items():
        a = agg[mid]
        total_obj = a["sustained"] + a["overruled"]
        obj_pct = round(100 * a["sustained"] / total_obj, 1) if total_obj else None
        ref_pct = (
            round(100 * a["refused_episodes"] / a["episodes"], 1)
            if meta.get("is_deepseek") and a["episodes"] else None
        )
        avg_sus = round(a["sustained_total"] / a["episodes"], 2) if a["episodes"] else 0.0
        entries.append(
            LeaderboardEntry(
                model_id=mid, display_name=meta["display_name"], season=meta["season"],
                win_count=a["wins"], objection_sustained_pct=obj_pct,
                explicit_refusal_pct=ref_pct, avg_sustained_per_episode=avg_sus,
                win_streak=a["streak"],
            )
        )
    entries.sort(key=lambda e: (-e.win_count, -(e.objection_sustained_pct or 0)))
    return entries
