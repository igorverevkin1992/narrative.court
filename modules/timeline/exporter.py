"""Timeline Exporter orchestration (Module 8).

Builds TimelineData from an Episode, then writes FCPXML (primary), EDL + markers
(fallback) into the episode's ``timeline/`` folder. Validates FCPXML
well-formedness before returning so failures surface immediately (Block O.4).
"""
from __future__ import annotations

from pathlib import Path

from modules.schemas import Episode, TimelineData
from modules.timeline import edl_generator, fcpxml_generator
from modules.timeline.timecode_calculator import build_timeline


def validate_fcpxml(xml: str) -> bool:
    """Return True if the FCPXML string is well-formed."""
    try:
        from lxml import etree  # type: ignore

        etree.fromstring(xml.encode("utf-8"))
        return True
    except Exception:
        try:
            import xml.etree.ElementTree as ET

            # Strip doctype line that stdlib parser rejects.
            cleaned = "\n".join(
                ln for ln in xml.splitlines() if not ln.strip().startswith("<!DOCTYPE")
            )
            ET.fromstring(cleaned)
            return True
        except Exception:
            return False


def export_timeline(
    episode: Episode,
    out_dir: str | Path,
    *,
    fps: int = 30,
    sample_rate: int = 44100,
) -> dict:
    """Generate and write all timeline artifacts.

    Returns a dict with absolute paths and the built TimelineData. Raises
    ValueError if the generated FCPXML is not well-formed.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    timeline: TimelineData = build_timeline(episode, fps=fps, sample_rate=sample_rate)
    episode.timeline_data = timeline

    fcpxml = fcpxml_generator.generate_fcpxml(episode, timeline)
    if not validate_fcpxml(fcpxml):
        raise ValueError("Generated FCPXML is not well-formed")

    edl = edl_generator.generate_edl(episode, timeline)
    markers_md = edl_generator.generate_markers_md(episode, timeline)

    fcpxml_path = out / f"{episode.slug}.fcpxml"
    edl_path = out / f"{episode.slug}.edl"
    markers_path = out / f"{episode.slug}_markers.md"

    fcpxml_path.write_text(fcpxml, encoding="utf-8")
    edl_path.write_text(edl, encoding="utf-8")
    markers_path.write_text(markers_md, encoding="utf-8")

    return {
        "fcpxml": str(fcpxml_path),
        "edl": str(edl_path),
        "markers_md": str(markers_path),
        "timeline": timeline,
    }
