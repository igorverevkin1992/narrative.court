"""Subtitle export (H3): SRT + WebVTT from the episode timeline.

Each clip's spoken text is split into short cues spread evenly across the clip's
on-timeline duration, so captions track the audio. Reuses the timecodes already
computed by ``build_timeline`` (``episode.timeline_data``) -- no new dependency.
"""
from __future__ import annotations

from modules.config import Config
from modules.schemas import Episode

_WORDS_PER_CUE = 8


def _split_words(text: str, n: int = _WORDS_PER_CUE) -> list[str]:
    words = text.split()
    chunks = [" ".join(words[i:i + n]) for i in range(0, len(words), n)]
    return chunks or [text.strip()]


def _fmt(seconds: float, sep: str) -> str:
    """Seconds -> 'HH:MM:SS<sep>mmm' (sep ',' for SRT, '.' for VTT)."""
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def build_cues(episode: Episode) -> list[tuple[float, float, str]]:
    """(start_sec, end_sec, text) for every cue, in timeline order.

    Raises ValueError if no timeline has been built yet (run export first)."""
    td = getattr(episode, "timeline_data", None)
    if not td or not td.clips:
        raise ValueError("No timeline; build the timeline (export) before subtitles.")
    fps = td.fps or 30
    cues: list[tuple[float, float, str]] = []
    for clip in td.clips:
        reps = episode.rounds.get(clip.clip_id) or []
        text = ((reps[0].used_text or reps[0].text).strip() if reps else "")
        if not text:
            continue
        start = clip.start_frames / fps
        dur = max(0.1, clip.duration_frames / fps)
        chunks = _split_words(text)
        per = dur / len(chunks)
        for i, chunk in enumerate(chunks):
            cues.append((start + i * per, start + (i + 1) * per, chunk))
    return cues


def render_srt(cues: list[tuple[float, float, str]]) -> str:
    out: list[str] = []
    for i, (a, b, t) in enumerate(cues, 1):
        out += [str(i), f"{_fmt(a, ',')} --> {_fmt(b, ',')}", t, ""]
    return "\n".join(out)


def render_vtt(cues: list[tuple[float, float, str]]) -> str:
    out: list[str] = ["WEBVTT", ""]
    for a, b, t in cues:
        out += [f"{_fmt(a, '.')} --> {_fmt(b, '.')}", t, ""]
    return "\n".join(out)


def build_subtitles(episode: Episode, config: Config) -> dict:
    """Write script/subtitles.srt and .vtt. Returns {srt, vtt, cues}."""
    from modules.episodes.manager import episode_dir  # local import avoids a cycle

    out_dir = episode_dir(episode, config) / "script"
    out_dir.mkdir(parents=True, exist_ok=True)
    cues = build_cues(episode)
    srt_path = out_dir / "subtitles.srt"
    vtt_path = out_dir / "subtitles.vtt"
    srt_path.write_text(render_srt(cues), encoding="utf-8")
    vtt_path.write_text(render_vtt(cues), encoding="utf-8")
    return {"srt": str(srt_path), "vtt": str(vtt_path), "cues": len(cues)}
