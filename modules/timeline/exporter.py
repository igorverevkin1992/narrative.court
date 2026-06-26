"""Timeline Exporter orchestration (Module 8).

Builds TimelineData from an Episode, then writes FCPXML (primary), EDL + markers
(fallback) into the episode's ``timeline/`` folder. Validates FCPXML
well-formedness before returning so failures surface immediately (Block O.4).
"""
from __future__ import annotations

from pathlib import Path

from modules.schemas import Episode, TimelineData
from modules.timeline import edl_generator, fcpxml_generator, otio_generator
from modules.timeline.timecode_calculator import build_timeline


def validate_fcpxml(xml: str) -> bool:
    """Return True if the FCPXML string is well-formed.

    Uses a hardened parser (no external DTD load, no entity resolution, no
    network) so validation can never be turned into an XXE/SSRF vector.
    """
    try:
        from lxml import etree  # type: ignore

        parser = etree.XMLParser(
            resolve_entities=False, no_network=True, load_dtd=False, dtd_validation=False,
        )
        etree.fromstring(xml.encode("utf-8"), parser)
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
    emit_otio: bool = False,
) -> dict:
    """Generate and write all timeline artifacts (resilient, Block O.4).

    The EDL + markers fallback is written FIRST so a malformed FCPXML can never
    leave the operator without an importable timeline. The primary FCPXML is then
    written and validated; if it is not well-formed the file is kept for
    inspection but ``fcpxml_valid`` is ``False`` (no exception is raised).

    Returns a dict with absolute paths, ``fcpxml_valid``, and the TimelineData.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    timeline: TimelineData = build_timeline(episode, fps=fps, sample_rate=sample_rate)
    episode.timeline_data = timeline

    # Guaranteed-importable fallback first.
    edl_path = out / f"{episode.slug}.edl"
    markers_path = out / f"{episode.slug}_markers.md"
    edl_path.write_text(edl_generator.generate_edl(episode, timeline), encoding="utf-8")
    markers_path.write_text(edl_generator.generate_markers_md(episode, timeline), encoding="utf-8")

    # Primary FCPXML: keep the file even if invalid, but flag it.
    fcpxml = fcpxml_generator.generate_fcpxml(episode, timeline)
    fcpxml_valid = validate_fcpxml(fcpxml)
    fcpxml_path = out / f"{episode.slug}.fcpxml"
    fcpxml_path.write_text(fcpxml, encoding="utf-8")

    otio_path = None
    if emit_otio:  # optional third format; DaVinci imports .otio natively (I8)
        otio_path = out / f"{episode.slug}.otio"
        otio_path.write_text(otio_generator.generate_otio(episode, timeline), encoding="utf-8")

    return {
        "fcpxml": str(fcpxml_path),
        "fcpxml_valid": fcpxml_valid,
        "edl": str(edl_path),
        "markers_md": str(markers_path),
        "otio": str(otio_path) if otio_path else None,
        "timeline": timeline,
    }
