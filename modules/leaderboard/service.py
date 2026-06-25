"""Leaderboard service (Block I.2) -- glue between saved episodes and the engine.

Reads episodes from the JSON store (the episode source of truth), turns the ones
that carry a recorded Oxford delta into EpisodeOutcomes, recomputes all five
metrics, persists the aggregate to SQLite, and writes Markdown + CSV exports.
"""
from __future__ import annotations

from pathlib import Path

from modules.config import Config
from modules.episodes.manager import list_saved_episodes
from modules.leaderboard.engine import (
    EpisodeOutcome,
    model_meta_from_config,
    outcome_from_episode,
    recompute,
)
from modules.leaderboard.export import to_csv, to_markdown
from modules.schemas import Episode, EpisodeStatus, LeaderboardEntry, OxfordDelta, Side


def record_delta(episode: Episode, delta: OxfordDelta) -> dict:
    """Attach an Oxford delta + computed winner to the episode (in memory).

    Returns the leaderboard_result dict that callers should persist via
    ``manager.save_episode``.
    """
    pros, deff = episode.prosecution_model_id, episode.defense_model_id
    side = delta.winner_side
    winner = None if side is None else (pros if side == Side.PROSECUTION else deff)
    episode.leaderboard_result = {
        "delta": delta.model_dump(mode="json"),
        "winner_model_id": winner,
        "winner_side": None if side is None else side.value,
        "no_quorum": delta.no_quorum,
    }
    return episode.leaderboard_result


def episodes_pending_delta(config: Config) -> list[Episode]:
    """Block O.5: exported/published episodes with no recorded Oxford delta.

    Used for a soft warning (never a hard block) when the operator starts the
    next episode while the previous one's jury result is still open.
    """
    pending: list[Episode] = []
    for ep in list_saved_episodes(config):
        if ep.status in (EpisodeStatus.EXPORTED, EpisodeStatus.PUBLISHED):
            if not (ep.leaderboard_result or {}).get("delta"):
                pending.append(ep)
    return pending


def collect_outcomes(config: Config) -> list[EpisodeOutcome]:
    """Build outcomes from every saved episode that has a recorded delta."""
    outcomes: list[EpisodeOutcome] = []
    # Win-streak is order-sensitive: process episodes oldest -> newest so the
    # streak reflects the most recent run (list_saved_episodes is newest-first).
    for episode in sorted(list_saved_episodes(config), key=lambda e: e.created_at):
        lr = episode.leaderboard_result or {}
        delta_data = lr.get("delta")
        if not delta_data:
            continue
        try:
            delta = OxfordDelta(**delta_data)
        except Exception:
            continue
        outcomes.append(outcome_from_episode(episode, delta))
    return outcomes


def recompute_and_persist(config: Config) -> list[LeaderboardEntry]:
    """Recompute the whole leaderboard and persist the aggregate (best-effort)."""
    outcomes = collect_outcomes(config)
    entries = recompute(outcomes, model_meta_from_config(config.models))
    try:
        from modules.db import save_leaderboard

        save_leaderboard(entries)
    except Exception:
        pass  # DB optional (e.g. in tests without init_db)
    return entries


def export_files(
    config: Config,
    entries: list[LeaderboardEntry],
    *,
    season: int,
    episode_no: int,
    total: int,
    title: str = "",
) -> dict:
    """Write leaderboard_s<season>.md and .csv into exports_dir."""
    exports = config.resolve_path("exports_dir")
    exports.mkdir(parents=True, exist_ok=True)
    md = to_markdown(entries, season, episode_no, total, title)
    csv_text = to_csv(entries)
    md_path = exports / f"leaderboard_s{season}.md"
    csv_path = exports / f"leaderboard_s{season}.csv"
    md_path.write_text(md, encoding="utf-8")
    csv_path.write_text(csv_text, encoding="utf-8")
    return {"md": str(md_path), "csv": str(csv_path), "md_text": md}
