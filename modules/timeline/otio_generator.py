"""OpenTimelineIO export (I8), an optional third format alongside FCPXML/EDL.

DaVinci Resolve imports ``.otio`` natively. Built by hand as schema-correct OTIO
JSON (no heavy ``opentimelineio`` dependency, so CI stays lean). Two sequential
audio tracks (PROSECUTION, DEFENSE) with gaps between clips; behaviour/objection
markers attach to the timeline Stack at their global timecode.
"""
from __future__ import annotations

import json

from modules.schemas import Episode, TimelineData


def _rt(value: int, rate: int) -> dict:
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": int(value)}


def _tr(start: int, dur: int, rate: int) -> dict:
    return {"OTIO_SCHEMA": "TimeRange.1", "start_time": _rt(start, rate), "duration": _rt(dur, rate)}


def _track(name: str, clips: list, fps: int) -> dict:
    children: list[dict] = []
    cursor = 0
    for c in sorted(clips, key=lambda x: x.start_frames):
        if c.start_frames > cursor:  # fill the silence before this clip with a gap
            children.append({"OTIO_SCHEMA": "Gap.1", "name": "gap",
                             "source_range": _tr(0, c.start_frames - cursor, fps)})
        clip = {"OTIO_SCHEMA": "Clip.1", "name": c.clip_id,
                "source_range": _tr(0, c.duration_frames, fps)}
        if c.audio_path:
            clip["media_reference"] = {"OTIO_SCHEMA": "ExternalReference.1",
                                       "target_url": "file://" + str(c.audio_path)}
        children.append(clip)
        cursor = c.start_frames + c.duration_frames
    return {"OTIO_SCHEMA": "Track.1", "name": name, "kind": "Audio", "children": children}


def generate_otio(episode: Episode, timeline: TimelineData) -> str:
    """Return a complete OTIO Timeline document as a JSON string."""
    fps = timeline.fps
    pros = [c for c in timeline.clips if c.track == "PROSECUTION"]
    deff = [c for c in timeline.clips if c.track == "DEFENSE"]
    markers = [
        {"OTIO_SCHEMA": "Marker.1", "name": m.marker_type, "color": "RED",
         "marked_range": _tr(m.frame, 1, fps), "metadata": {"note": m.note}}
        for m in timeline.markers
    ]
    stack = {
        "OTIO_SCHEMA": "Stack.1", "name": "tracks",
        "children": [_track("PROSECUTION", pros, fps), _track("DEFENSE", deff, fps)],
        "markers": markers,
    }
    timeline_obj = {
        "OTIO_SCHEMA": "Timeline.1", "name": episode.slug,
        "global_start_time": _rt(0, fps), "tracks": stack,
    }
    return json.dumps(timeline_obj, indent=2)
