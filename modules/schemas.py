"""Pydantic v2 domain models for The Narrative Court.

This module is the single source of truth for every domain object referenced
across the system. It mirrors Section 4 of TZ_Narrative_Court.md.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Episode slug: filesystem- and XML-safe (no path separators, dots, spaces).
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")


# --------------------------------------------------------------------------- #
# Enums and shared literal types
# --------------------------------------------------------------------------- #
class EpisodeStatus(str, Enum):
    DRAFT = "draft"
    SMOKE_TESTED = "smoke_tested"
    GENERATED = "generated"
    TTS_DONE = "tts_done"
    EXPORTED = "exported"
    PUBLISHED = "published"


class TopicStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    REJECTED = "rejected"


class Side(str, Enum):
    PROSECUTION = "prosecution"
    DEFENSE = "defense"


FlagType = Literal["WEAK", "EVASIVE", "REFUSED", "SUPPRESSED"]
DeepSeekRisk = Literal["low", "medium", "high", "guaranteed_refused"]
MonetizationRisk = Literal["green", "yellow", "red"]
TrackName = Literal["HOST_VOICE", "PROSECUTION", "DEFENSE", "SFX_MARKERS", "MUSIC_BED"]


# --------------------------------------------------------------------------- #
# Generation and behaviour
# --------------------------------------------------------------------------- #
class GenerationResult(BaseModel):
    content: str
    reasoning_content: str | None = None      # DeepSeek/Claude CoT, when exposed
    finish_reason: str = "stop"               # stop | length | content_filter | ...
    model_id: str
    model_version: str | None = None          # echoed by provider when available
    latency_ms: int = 0
    prompt_hash: str = ""                      # sha256(system + "\x00" + user)
    usage: dict | None = None
    raw: dict | None = None


class BehaviourFlag(BaseModel):
    flag_type: FlagType
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str
    rule_triggered: str
    model_id: str
    round_id: str
    timestamp: datetime = Field(default_factory=_utcnow)


class GenerationLog(BaseModel):
    log_id: UUID = Field(default_factory=uuid4)
    episode_id: UUID
    model_id: str
    round_id: str
    attempt_number: int
    timestamp: datetime = Field(default_factory=_utcnow)
    system_prompt: str
    user_prompt: str
    temperature: float
    seed: int | None = None
    result: GenerationResult
    selected: bool = False
    selection_policy: Literal["first_valid", "manual"] = "first_valid"


class TranslationResult(BaseModel):
    original_text: str
    corrected_text: str
    drift_detected: bool
    drift_score: float | None = None
    used_text: str
    correction_applied: bool


class TTSPreset(BaseModel):
    llm_model_id: str
    el_voice_id: str
    stability: float = Field(ge=0.0, le=1.0, default=0.5)
    similarity_boost: float = Field(ge=0.0, le=1.0, default=0.75)
    style: float = Field(ge=0.0, le=1.0, default=0.3)
    use_speaker_boost: bool = True
    el_model_id: str = "eleven_turbo_v2_5"
    character_notes: str = ""


# --------------------------------------------------------------------------- #
# Episode composition and timeline
# --------------------------------------------------------------------------- #
class Replica(BaseModel):
    round_id: str
    side: Side
    model_id: str
    text: str
    used_text: str | None = None              # post translation/truncation, fed to TTS
    audio_path: str | None = None
    duration_sec: float | None = None
    flags: list[BehaviourFlag] = Field(default_factory=list)
    variants: list[str] = Field(default_factory=list)   # alternative takes (I1)


class QuickfireExchange(BaseModel):
    question: str
    prosecution_answer: str
    defense_answer: str
    variability_score: float = 0.0
    recommended: bool = False
    over_limit: bool = False


class ObjectionEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    round_id: str
    side: Side
    claim: str
    ruling: Literal["sustained", "overruled", "pending"] = "pending"
    timestamp: datetime | None = None


class TimelineClip(BaseModel):
    clip_id: str
    track: TrackName
    lane: int
    audio_path: str | None = None             # None => placeholder gap
    start_frames: int
    duration_frames: int


class TimelineMarker(BaseModel):
    marker_type: str
    frame: int
    track: str = "SFX_MARKERS"
    note: str = ""


class TimelineData(BaseModel):
    fps: int = 30
    sample_rate: int = 44100
    clips: list[TimelineClip] = Field(default_factory=list)
    markers: list[TimelineMarker] = Field(default_factory=list)
    total_frames: int = 0


class GenParams(BaseModel):
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=800, ge=1, le=8192)
    quickfire_max_tokens: int = Field(default=60, ge=1, le=8192)
    seed: int | None = None


class Episode(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=_utcnow)
    status: EpisodeStatus = EpisodeStatus.DRAFT
    thesis: str
    slug: str
    topic_id: UUID | None = None
    prosecution_model_id: str
    defense_model_id: str
    gen_params: GenParams = Field(default_factory=GenParams)
    rounds: dict[str, list[Replica]] = Field(default_factory=dict)
    quickfire: list[QuickfireExchange] = Field(default_factory=list)
    behaviour_flags: list[BehaviourFlag] = Field(default_factory=list)
    objections: list[ObjectionEvent] = Field(default_factory=list)
    tts_files: list[str] = Field(default_factory=list)
    timeline_data: TimelineData | None = None
    leaderboard_result: dict | None = None
    verdict: dict | None = None          # H4: honesty-judge talking points for the host
    youtube_metadata: dict = Field(default_factory=dict)

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        """Reject path-traversal / unsafe slugs (used in fs paths, FCPXML, EDL)."""
        if not SLUG_RE.match(v):
            raise ValueError(
                "slug must match ^[a-z0-9][a-z0-9_]{0,63}$ "
                "(lowercase letters, digits, underscore; no spaces, dots, slashes)"
            )
        return v


# --------------------------------------------------------------------------- #
# Topics, checklist, leaderboard, Oxford delta
# --------------------------------------------------------------------------- #
class TopicChecklist(BaseModel):
    has_evidence_both_sides: bool
    is_debatable: bool
    deepseek_risk: DeepSeekRisk
    monetization_risk: MonetizationRisk
    reach_vs_safety: int = Field(ge=1, le=5)
    freshness_vs_evergreen: int = Field(ge=1, le=5)
    auto_status: Literal["approved", "warning", "rejected"] = "warning"

    @model_validator(mode="after")
    def _compute_status(self) -> "TopicChecklist":
        if self.deepseek_risk == "guaranteed_refused":
            self.auto_status = "rejected"
        elif (
            self.has_evidence_both_sides
            and self.is_debatable
            and self.deepseek_risk in ("low", "medium")
        ):
            self.auto_status = "approved"
        else:
            self.auto_status = "warning"
        return self


class QuickfireQuestion(BaseModel):
    id: str
    text: str


class Topic(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    thesis: str
    thesis_variants: list[str] = Field(default_factory=list, max_length=3)
    category: Literal["safe", "optimal", "hot"]
    monetization_risk: MonetizationRisk
    deepseek_risk: DeepSeekRisk
    recommended_pair: tuple[str, str]
    factual_anchors: list[str] = Field(default_factory=list)
    quickfire_bank: list[QuickfireQuestion] = Field(default_factory=list)
    status: TopicStatus = TopicStatus.DRAFT
    episode_id: UUID | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    checklist: TopicChecklist
    notes: str = ""


class OxfordDelta(BaseModel):
    episode_id: UUID
    agree_before: float = Field(ge=0.0, le=100.0)
    agree_after: float = Field(ge=0.0, le=100.0)
    disagree_before: float = Field(ge=0.0, le=100.0)
    disagree_after: float = Field(ge=0.0, le=100.0)
    votes_before: int = Field(ge=0)
    votes_after: int = Field(ge=0)

    @property
    def no_quorum(self) -> bool:
        return self.votes_before < 30 or self.votes_after < 30

    @property
    def delta_prosecution(self) -> float:
        return self.agree_after - self.agree_before

    @property
    def delta_defense(self) -> float:
        return self.disagree_after - self.disagree_before

    @property
    def winner_side(self) -> Side | None:
        if self.no_quorum:
            return None
        return (
            Side.PROSECUTION
            if self.delta_prosecution >= self.delta_defense
            else Side.DEFENSE
        )


class LeaderboardEntry(BaseModel):
    model_id: str
    display_name: str
    season: int
    win_count: int = 0
    objection_sustained_pct: float | None = None
    explicit_refusal_pct: float | None = None   # only DeepSeek; None => n/a
    avg_sustained_per_episode: float = 0.0
    win_streak: int = 0


# --------------------------------------------------------------------------- #
# Smoke test result
# --------------------------------------------------------------------------- #
class SmokeTestResult(BaseModel):
    passed: bool
    flags: list[BehaviourFlag] = Field(default_factory=list)
    reframe: str | None = None
    attempt: int = 1
    detail: str = ""
