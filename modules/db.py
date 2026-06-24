"""Persistence layer (SQLAlchemy 2.x over SQLite).

Implements the core tables from the DDL in Section 4.7 of the TZ. Full Episode
and Topic objects are stored as ``payload_json`` (their Pydantic dump) alongside
queryable columns; behaviour flags / objections / leaderboard get their own rows.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)


class Base(DeclarativeBase):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelRow(Base):
    __tablename__ = "models"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    api_format: Mapped[str] = mapped_column(String)
    model_name: Mapped[str] = mapped_column(String)
    season: Mapped[int] = mapped_column(Integer)
    translation_layer: Mapped[int] = mapped_column(Integer, default=0)
    sanctions_risk: Mapped[int] = mapped_column(Integer, default=0)


class EpisodeRow(Base):
    __tablename__ = "episodes"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    slug: Mapped[str] = mapped_column(String, unique=True)
    thesis: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="draft")
    prosecution_model_id: Mapped[str] = mapped_column(String)
    defense_model_id: Mapped[str] = mapped_column(String)
    season: Mapped[int] = mapped_column(Integer, default=1)
    episode_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String, default=_now)
    published_at: Mapped[str | None] = mapped_column(String, nullable=True)


class TopicRow(Base):
    __tablename__ = "topics"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    thesis: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String)
    monetization_risk: Mapped[str] = mapped_column(String)
    deepseek_risk: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="draft")
    auto_status: Mapped[str | None] = mapped_column(String, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String, default=_now)


class LeaderboardRow(Base):
    __tablename__ = "leaderboard_aggregate"
    model_id: Mapped[str] = mapped_column(String, ForeignKey("models.id"), primary_key=True)
    season: Mapped[int] = mapped_column(Integer)
    win_count: Mapped[int] = mapped_column(Integer, default=0)
    objection_sustained_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    explicit_refusal_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_sustained_per_episode: Mapped[float] = mapped_column(Float, default=0.0)
    win_streak: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[str] = mapped_column(String, default=_now)


_SESSION_FACTORY: sessionmaker | None = None


def init_db(db_path: str | Path) -> sessionmaker:
    """Create the SQLite file (if needed), all tables, and a session factory."""
    global _SESSION_FACTORY
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    _SESSION_FACTORY = sessionmaker(bind=engine, future=True)
    return _SESSION_FACTORY


def get_session() -> Session:
    if _SESSION_FACTORY is None:
        raise RuntimeError("init_db() must be called before get_session()")
    return _SESSION_FACTORY()


def seed_models_from_config(models: list[dict]) -> int:
    """Insert/update the models registry from config.yaml. Returns row count."""
    with get_session() as s:
        for m in models:
            row = s.get(ModelRow, m["id"])
            if row is None:
                row = ModelRow(id=m["id"])
                s.add(row)
            row.display_name = m["display_name"]
            row.provider = m["provider"]
            row.api_format = m["api_format"]
            row.model_name = m["model_name"]
            row.season = m["season"]
            row.translation_layer = int(bool(m.get("translation_layer")))
            row.sanctions_risk = int(bool(m.get("sanctions_risk")))
        s.commit()
        return s.query(ModelRow).count() if hasattr(s, "query") else len(models)


def save_episode(episode) -> None:
    """Upsert an Episode (Pydantic) into the episodes table."""
    with get_session() as s:
        row = s.get(EpisodeRow, str(episode.id))
        if row is None:
            row = EpisodeRow(id=str(episode.id))
            s.add(row)
        row.slug = episode.slug
        row.thesis = episode.thesis
        row.status = episode.status.value
        row.prosecution_model_id = episode.prosecution_model_id
        row.defense_model_id = episode.defense_model_id
        row.payload_json = episode.model_dump_json()
        s.commit()


def save_leaderboard(entries) -> None:
    """Upsert recomputed LeaderboardEntry rows into leaderboard_aggregate."""
    with get_session() as s:
        for e in entries:
            row = s.get(LeaderboardRow, e.model_id)
            if row is None:
                row = LeaderboardRow(model_id=e.model_id)
                s.add(row)
            row.season = e.season
            row.win_count = e.win_count
            row.objection_sustained_pct = e.objection_sustained_pct
            row.explicit_refusal_pct = e.explicit_refusal_pct
            row.avg_sustained_per_episode = e.avg_sustained_per_episode
            row.win_streak = e.win_streak
            row.updated_at = _now()
        s.commit()


def delete_topic(topic_id: str) -> None:
    """Remove a topic row by id (Topic Bank delete action)."""
    with get_session() as s:
        row = s.get(TopicRow, topic_id)
        if row is not None:
            s.delete(row)
            s.commit()
