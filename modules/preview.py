"""Episode rough-cut preview (G3).

Concatenate the per-round WAV clips, in broadcast order, into a single
``audio/preview.wav`` so the operator can listen to the whole episode in the UI
before opening DaVinci. Pure stdlib ``wave`` -- no resampling and no new
dependency; every clip is the mono 16-bit PCM the TTS engine writes, so the
output format simply follows the first clip.
"""
from __future__ import annotations

import wave
from pathlib import Path

from modules.config import Config
from modules.schemas import Episode
from modules.timeline.timecode_calculator import _ordered_round_ids


def _clip_path(episode: Episode, round_id: str, audio_dir: Path) -> Path | None:
    """Resolve a round's WAV: prefer the replica's recorded path, else <rid>.wav."""
    reps = episode.rounds.get(round_id) or []
    if reps and reps[0].audio_path and Path(reps[0].audio_path).exists():
        return Path(reps[0].audio_path)
    p = audio_dir / f"{round_id}.wav"
    return p if p.exists() else None


def build_preview(episode: Episode, config: Config, *, gap_sec: float = 0.4) -> dict:
    """Build ``audio/preview.wav`` from the round clips in speaking order.

    Returns ``{path, clips, missing, duration_sec}``. Clips are ordered with the
    same ``_ordered_round_ids`` the timeline uses, so the preview matches the
    edit. Raises ``ValueError`` if no clip exists yet (run TTS first).
    """
    from modules.episodes.manager import episode_dir  # local import avoids a cycle

    audio_dir = episode_dir(episode, config) / "audio"
    order = _ordered_round_ids(episode.rounds)

    paths: list[Path] = []
    missing: list[str] = []
    for rid in order:
        cp = _clip_path(episode, rid, audio_dir)
        if cp is not None:
            paths.append(cp)
        else:
            missing.append(rid)

    if not paths:
        raise ValueError("No audio clips found; run TTS before building a preview.")

    # Output format follows the first clip (engine writes uniform mono PCM16).
    with wave.open(str(paths[0]), "rb") as w0:
        nchannels, sampwidth, framerate = w0.getnchannels(), w0.getsampwidth(), w0.getframerate()

    gap_frames = int(max(0.0, gap_sec) * framerate)
    silence = b"\x00" * (gap_frames * nchannels * sampwidth)

    out_path = audio_dir / "preview.wav"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".wav.tmp")
    total_frames = 0
    with wave.open(str(tmp), "wb") as out:
        out.setnchannels(nchannels)
        out.setsampwidth(sampwidth)
        out.setframerate(framerate)
        for i, p in enumerate(paths):
            with wave.open(str(p), "rb") as w:
                frames = w.readframes(w.getnframes())
            out.writeframes(frames)
            total_frames += len(frames) // (nchannels * sampwidth)
            if gap_frames and i < len(paths) - 1:
                out.writeframes(silence)
                total_frames += gap_frames
    tmp.replace(out_path)  # atomic

    return {
        "path": str(out_path),
        "clips": len(paths),
        "missing": missing,
        "duration_sec": round(total_frames / float(framerate), 3) if framerate else 0.0,
    }
