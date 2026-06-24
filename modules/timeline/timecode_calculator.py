"""Timecode calculation (Block H.3).

Measures WAV durations and lays replicas out on a single global timeline cursor,
assigning each clip to its side's track/lane and placing behaviour markers.

Duration backend preference: soundfile (libsndfile) -> mutagen -> stdlib wave.
The stdlib ``wave`` fallback means the tested path needs no system libraries.
"""
from __future__ import annotations

import wave
from pathlib import Path

from modules.schemas import (
    Episode,
    TimelineClip,
    TimelineData,
    TimelineMarker,
)



def audio_duration_sec(path: str | Path) -> float:
    """Return WAV duration in seconds. Tries soundfile, mutagen, then wave."""
    p = str(path)
    # 1) soundfile (preferred per TZ; libsndfile, no ffmpeg)
    try:
        import soundfile as sf  # type: ignore

        info = sf.info(p)
        return float(info.frames) / float(info.samplerate)
    except Exception:
        pass
    # 2) mutagen (pure-python fallback)
    try:
        from mutagen.wave import WAVE  # type: ignore

        audio = WAVE(p)
        return float(audio.info.length)
    except Exception:
        pass
    # 3) stdlib wave (always available; PCM WAV only)
    with wave.open(p, "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        return frames / float(rate) if rate else 0.0


def seconds_to_frames(seconds: float, fps: int) -> int:
    return int(round(seconds * fps))


def frames_to_timecode(frames: int, fps: int) -> str:
    """Frames -> 'HH:MM:SS:FF' non-drop timecode."""
    total_seconds, f = divmod(int(frames), fps)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def _track_for_side(side: str) -> tuple[str, int]:
    """Map a replica side to (track_name, fcpxml_lane)."""
    if side == "prosecution":
        return "PROSECUTION", -1
    return "DEFENSE", -2


def _round_sort_key(rid: str) -> tuple[int, int, int]:
    """Speaking order: round number, then sub-index (qNN/pN), then side.

    Examples: r1_prosecution -> (1,0,0); r1_defense -> (1,0,1);
    r2_q01_prosecution -> (2,1,0); r3_p2_defense -> (3,2,1); r4_defense -> (4,0,1).
    """
    parts = rid.split("_")
    rnum = int(parts[0][1:]) if parts and parts[0][1:].isdigit() else 99
    sub = 0
    side_rank = 0
    for p in parts[1:]:
        if (p.startswith("q") or p.startswith("p")) and p[1:].isdigit():
            sub = int(p[1:])
        elif p in ("prosecution", "pros"):
            side_rank = 0
        elif p in ("defense", "def"):
            side_rank = 1
    return (rnum, sub, side_rank)


def _ordered_round_ids(rounds: dict) -> list[str]:
    """Sort round ids into canonical speaking order."""
    return sorted(rounds.keys(), key=_round_sort_key)


def build_timeline(episode: Episode, fps: int = 30, sample_rate: int = 44100) -> TimelineData:
    """Walk replicas in speaking order, advancing one global cursor (Block H.3).

    Each replica becomes a TimelineClip on its side's track. Behaviour flags and
    objections become markers at the clip start (+ a small relative offset).
    """
    clips: list[TimelineClip] = []
    markers: list[TimelineMarker] = []
    cursor = 0  # global cursor, in frames
    seen_rounds: set[int] = set()

    for rid in _ordered_round_ids(episode.rounds):
        rnum = _round_sort_key(rid)[0]
        for replica in episode.rounds[rid]:
            duration = replica.duration_sec
            if duration is None and replica.audio_path:
                try:
                    duration = audio_duration_sec(replica.audio_path)
                except Exception:
                    duration = 0.0
            duration = duration or 0.0
            dur_frames = seconds_to_frames(duration, fps)
            track, lane = _track_for_side(replica.side.value)

            clips.append(
                TimelineClip(
                    clip_id=rid,
                    track=track,
                    lane=lane,
                    audio_path=replica.audio_path,
                    start_frames=cursor,
                    duration_frames=dur_frames,
                )
            )

            # Round start marker: exactly one per distinct round number.
            if rnum not in seen_rounds and rnum != 99:
                seen_rounds.add(rnum)
                markers.append(
                    TimelineMarker(
                        marker_type=f"ROUND_START_{rnum}",
                        frame=cursor, track="SFX_MARKERS", note=rid,
                    )
                )

            # Behaviour flags -> markers (offset proportional to evidence position).
            for flag in replica.flags:
                rel = min(dur_frames - 1, max(0, dur_frames // 3)) if dur_frames else 0
                markers.append(
                    TimelineMarker(
                        marker_type=flag.flag_type,
                        frame=cursor + rel, track="SFX_MARKERS",
                        note=f"{flag.model_id}: {flag.evidence[:60]}",
                    )
                )

            cursor += dur_frames

    # Objection events -> markers (placed at the start of their round's clip).
    clip_starts = {c.clip_id: c.start_frames for c in clips}
    for obj in episode.objections:
        base = clip_starts.get(obj.round_id, 0)
        mtype = (
            "OBJECTION_SUSTAINED" if obj.ruling == "sustained"
            else "OBJECTION_OVERRULED" if obj.ruling == "overruled"
            else "OBJECTION_PENDING"
        )
        markers.append(
            TimelineMarker(marker_type=mtype, frame=base, track="SFX_MARKERS", note=obj.claim[:60])
        )

    return TimelineData(
        fps=fps,
        sample_rate=sample_rate,
        clips=clips,
        markers=markers,
        total_frames=cursor,
    )
