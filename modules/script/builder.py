"""Script Builder (Module 7).

Assembles the human-readable script, host cues, and machine-readable behaviour
flags into the episode's ``script/`` folder.
"""
from __future__ import annotations

import json
from pathlib import Path

from modules.schemas import Episode
from modules.timeline.timecode_calculator import _round_sort_key, frames_to_timecode

_FLAG_GLYPH = {
    "REFUSED": "[REFUSED]", "SUPPRESSED": "[SUPPRESSED]",
    "EVASIVE": "[EVASIVE]", "WEAK": "[WEAK]",
}

HOST_CUES_TEMPLATE = """# Host cues - {slug}

## Block 2 - Introduction & context (0:20-3:00) [HOST VOICE]
- Introduce the motion: "{thesis}"
- Set the courtroom frame: prosecution argues the motion stands, defense argues it fails.
- Remind viewers: you judge honesty of argument, not truth.

## Block 8 - Host verdict (20:00-22:00) [HOST VOICE]
- Summarize the strongest honest argument from each side.
- Call out any [REFUSED]/[SUPPRESSED]/[EVASIVE]/[WEAK] moments flagged in the script.
- Do NOT declare a truth winner; rate honesty of argumentation.
"""


def _clip_timecode(episode: Episode, round_id: str) -> str:
    if not episode.timeline_data:
        return "--:--:--:--"
    for clip in episode.timeline_data.clips:
        if clip.clip_id == round_id:
            return frames_to_timecode(clip.start_frames, episode.timeline_data.fps)
    return "--:--:--:--"


def build_script_md(episode: Episode) -> str:
    lines = [
        f"# {episode.slug} - Episode Script",
        "",
        f"**Motion:** {episode.thesis}",
        f"**Prosecution:** {episode.prosecution_model_id}  ",
        f"**Defense:** {episode.defense_model_id}",
        "",
    ]
    if not episode.timeline_data:
        lines.append("> TIMECODES PENDING (run TTS + timeline export to populate)\n")

    for rid in sorted(episode.rounds.keys(), key=_round_sort_key):
        for replica in episode.rounds[rid]:
            tc = _clip_timecode(episode, rid)
            flags = " ".join(_FLAG_GLYPH.get(f.flag_type, "") for f in replica.flags)
            header = f"### [{tc}] {rid} -- {replica.side.value} ({replica.model_id}) {flags}".rstrip()
            lines.append(header)
            lines.append((replica.used_text or replica.text).strip())
            lines.append("")

    if episode.quickfire:
        lines.append("## Quickfire (selected)")
        for i, ex in enumerate(episode.quickfire, 1):
            if not ex.recommended:
                continue
            warn = " [>15s]" if ex.over_limit else ""
            lines.append(f"**Q{i} (var={ex.variability_score}){warn}:** {ex.question}")
            lines.append(f"- Prosecution: {ex.prosecution_answer}")
            lines.append(f"- Defense: {ex.defense_answer}")
            lines.append("")
    return "\n".join(lines) + "\n"


def write_script(episode: Episode, script_dir: str | Path) -> dict:
    """Write episode_script.md, host_cues.md, behaviour_flags.json. Return paths."""
    out = Path(script_dir)
    out.mkdir(parents=True, exist_ok=True)

    script_path = out / "episode_script.md"
    cues_path = out / "host_cues.md"
    flags_path = out / "behaviour_flags.json"

    script_path.write_text(build_script_md(episode), encoding="utf-8")
    cues_path.write_text(
        HOST_CUES_TEMPLATE.format(slug=episode.slug, thesis=episode.thesis),
        encoding="utf-8",
    )

    all_flags = list(episode.behaviour_flags)
    for replicas in episode.rounds.values():
        for r in replicas:
            all_flags.extend(r.flags)
    flags_path.write_text(
        json.dumps([f.model_dump(mode="json") for f in all_flags], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"script": str(script_path), "host_cues": str(cues_path), "flags": str(flags_path)}
