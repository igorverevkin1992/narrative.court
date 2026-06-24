"""FCPXML 1.11 generator (Block H.4), primary export for DaVinci Resolve 19.

Hand-built with lxml for full control over a Resolve-compatible structure:
one mono asset-clip per replica, placed on a negative lane per side, with
behaviour markers attached to the primary gap. Falls back to stdlib
ElementTree if lxml is unavailable (well-formedness is preserved either way).
"""
from __future__ import annotations

import re

from modules.schemas import Episode, TimelineData

try:
    from lxml import etree as _ET  # type: ignore

    _HAVE_LXML = True
except Exception:  # pragma: no cover - exercised only without lxml
    import xml.etree.ElementTree as _ET  # type: ignore

    _HAVE_LXML = False


_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _xml_safe(s: str) -> str:
    """Strip characters illegal in XML 1.0 (lxml escapes <>& on its own)."""
    return _ILLEGAL_XML.sub("", s or "")


def _t(frames: int, fps: int) -> str:
    """Rational FCPXML time, e.g. 240 frames @30 -> '240/30s'."""
    return f"{frames}/{fps}s"


def _role_for_track(track: str) -> str:
    if track == "PROSECUTION":
        return "dialogue.prosecution"
    if track == "DEFENSE":
        return "dialogue.defense"
    return "dialogue"


def generate_fcpxml(
    episode: Episode,
    timeline: TimelineData,
    *,
    project_name: str | None = None,
) -> str:
    """Return a complete FCPXML 1.11 document as a UTF-8 string."""
    fps = timeline.fps
    sr = str(timeline.sample_rate)

    fcpxml = _ET.Element("fcpxml", version="1.11")
    resources = _ET.SubElement(fcpxml, "resources")
    _ET.SubElement(
        resources, "format", id="r1", name="FFVideoFormat1080p30",
        frameDuration=f"1/{fps}s", width="1920", height="1080",
        colorSpace="1-1-1 (Rec. 709)",
    )

    # Pre-assign asset ids so the <asset> and <asset-clip> loops stay in sync.
    audio_clips = [c for c in timeline.clips if c.audio_path]
    clip_assets = [(c, f"a{i}") for i, c in enumerate(audio_clips, start=1)]

    for clip, aid in clip_assets:
        asset = _ET.SubElement(
            resources, "asset", id=aid, name=_xml_safe(clip.clip_id), start="0s",
            duration=_t(clip.duration_frames, fps), hasVideo="0", hasAudio="1",
            audioSources="1", audioChannels="1", audioRate=sr,
        )
        _ET.SubElement(
            asset, "media-rep", kind="original-media",
            src="file://" + str(clip.audio_path),
        )

    library = _ET.SubElement(fcpxml, "library")
    event = _ET.SubElement(library, "event", name="The Narrative Court")
    project = _ET.SubElement(event, "project", name=_xml_safe(project_name or episode.slug))
    sequence = _ET.SubElement(
        project, "sequence", format="r1", duration=_t(timeline.total_frames, fps),
        tcStart="0s", tcFormat="NDF", audioLayout="stereo", audioRate=sr,
    )
    spine = _ET.SubElement(sequence, "spine")
    gap = _ET.SubElement(
        spine, "gap", name="Timeline", offset="0s",
        duration=_t(max(timeline.total_frames, 1), fps), start="0s",
    )

    for clip, aid in clip_assets:
        _ET.SubElement(
            gap, "asset-clip", ref=aid, lane=str(clip.lane),
            offset=_t(clip.start_frames, fps), name=_xml_safe(clip.clip_id),
            duration=_t(clip.duration_frames, fps),
            audioRole=_role_for_track(clip.track),
        )

    for marker in timeline.markers:
        _ET.SubElement(
            gap, "marker", start=_t(marker.frame, fps),
            duration=_t(1, fps), value=_xml_safe(marker.marker_type),
        )

    if _HAVE_LXML:
        body = _ET.tostring(
            fcpxml, pretty_print=True, xml_declaration=True,
            encoding="UTF-8", doctype="<!DOCTYPE fcpxml>",
        ).decode("utf-8")
        return body
    # stdlib fallback: add declaration + doctype manually.
    raw = _ET.tostring(fcpxml, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + raw + "\n"
