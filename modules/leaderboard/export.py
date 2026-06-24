"""Leaderboard export to Markdown + CSV (Block I.3)."""
from __future__ import annotations

import csv
import io
from datetime import date

from modules.schemas import LeaderboardEntry


def _fmt_pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0f}%"


def to_markdown(entries: list[LeaderboardEntry], season: int, episode_no: int, total: int,
                last_episode_title: str = "") -> str:
    lines = [
        f"## Season {season} Standings - Episode {episode_no} of {total}",
        "",
        "| Model | Wins | Obj% | Ref% | Avg Sus | Streak |",
        "|-------|------|------|------|---------|--------|",
    ]
    for e in entries:
        if e.season != season:
            continue
        lines.append(
            f"| {e.display_name} | {e.win_count} | {_fmt_pct(e.objection_sustained_pct)} "
            f"| {_fmt_pct(e.explicit_refusal_pct)} | {e.avg_sustained_per_episode:.1f} "
            f"| {e.win_streak} |"
        )
    suffix = f' "{last_episode_title}"' if last_episode_title else ""
    lines += ["", f"Last updated: {date.today().isoformat()} after Ep.{episode_no}{suffix}"]
    return "\n".join(lines) + "\n"


def to_csv(entries: list[LeaderboardEntry]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "model_id", "display_name", "season", "win_count",
        "objection_sustained_pct", "explicit_refusal_pct",
        "avg_sustained_per_episode", "win_streak",
    ])
    for e in entries:
        writer.writerow([
            e.model_id, e.display_name, e.season, e.win_count,
            "" if e.objection_sustained_pct is None else e.objection_sustained_pct,
            "" if e.explicit_refusal_pct is None else e.explicit_refusal_pct,
            e.avg_sustained_per_episode, e.win_streak,
        ])
    return buf.getvalue()
