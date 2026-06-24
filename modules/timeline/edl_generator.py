"""EDL CMX3600 generator + companion markers .md (Block H.5), fallback export.

EDL is single-track and carries no markers, so a sibling ``*_markers.md`` file
lists every marker timecode for manual placement in DaVinci Resolve.
"""
from __future__ import annotations

import re
from pathlib import Path

from modules.schemas import Episode, TimelineData
from modules.timeline.timecode_calculator import frames_to_timecode


def generate_edl(episode: Episode, timeline: TimelineData) -> str:
    """Return a CMX3600 EDL string for all audio clips (sequential record TC)."""
    fps = timeline.fps
    lines: list[str] = [f"TITLE: {episode.slug}", "FCM: NON-DROP FRAME", ""]
    event_no = 0
    for clip in timeline.clips:
        if not clip.audio_path:
            continue
        event_no += 1
        src_in = frames_to_timecode(0, fps)
        src_out = frames_to_timecode(clip.duration_frames, fps)
        rec_in = frames_to_timecode(clip.start_frames, fps)
        rec_out = frames_to_timecode(clip.start_frames + clip.duration_frames, fps)
        reel = clip.clip_id[:8].ljust(8)
        lines.append(f"{event_no:03d}  {reel} AA     C        {src_in} {src_out} {rec_in} {rec_out}")
        name = Path(clip.audio_path).name
        lines.append(f"* FROM CLIP NAME: {name}")
    return "\n".join(lines) + "\n"


def generate_markers_md(episode: Episode, timeline: TimelineData) -> str:
    """Return a markdown table of marker timecodes for manual placement."""
    fps = timeline.fps
    out = [
        f"# Markers - {episode.slug} (place manually on a marker track)",
        "",
        "| Timecode | Marker | Track | Note |",
        "|----------|--------|-------|------|",
    ]
    for m in sorted(timeline.markers, key=lambda x: x.frame):
        tc = frames_to_timecode(m.frame, fps)
        # Collapse newlines/whitespace and neutralise pipes so model-derived
        # evidence text cannot break the markdown table row.
        note = re.sub(r"\s+", " ", (m.note or "")).replace("|", "/").strip()
        out.append(f"| {tc} | {m.marker_type} | {m.track} | {note} |")
    return "\n".join(out) + "\n"
