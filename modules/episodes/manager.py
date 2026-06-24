"""Episode Manager (Module 12).

Drives the full episode pipeline: generate rounds -> TTS -> timeline export ->
script -> metadata, advancing the episode status machine and writing the episode
folder. Designed to run end-to-end in offline mode (no API keys).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from modules.config import ROOT, Config, get_secret
from modules.generator.episode_generator import EpisodeGenerator
from modules.llm.orchestrator import Orchestrator
from modules.metadata.generator import generate_metadata
from modules.schemas import Episode, EpisodeStatus
from modules.script.builder import write_script
from modules.timeline.exporter import export_timeline
from modules.tts.engine import TTSEngine, TTSJob
from modules.tts.presets import load_presets


async def run_full_pipeline(
    episode: Episode,
    config: Config,
    *,
    offline: bool = True,
    on_log: Callable[[str], None] | None = None,
) -> dict:
    log = on_log or (lambda m: None)

    orch = Orchestrator(config, offline=offline)
    gen = EpisodeGenerator(config, orch, on_log=log)

    log("Generating rounds...")
    await gen.generate_rounds(episode)
    episode.status = EpisodeStatus.GENERATED

    episodes_dir = config.resolve_path("episodes_dir") / episode.slug
    audio_dir = episodes_dir / "audio"
    script_dir = episodes_dir / "script"
    timeline_dir = episodes_dir / "timeline"

    # --- TTS (resumable batch) ---
    presets_rel = config.tts_defaults.get("presets_file", "./config/tts_presets.json")
    presets_path = Path(presets_rel)
    if not presets_path.is_absolute():
        presets_path = ROOT / presets_rel.lstrip("./")
    presets = load_presets(presets_path)
    el_key = None if offline else get_secret("ELEVENLABS_API_KEY")
    sample_rate = int(config.get("timeline", "audio_sample_rate", default=44100))
    engine = TTSEngine(
        presets, el_key, offline=offline,
        output_format=config.tts_defaults.get("output_format", "pcm_44100"),
        sample_rate=sample_rate,
    )
    jobs: list[TTSJob] = []
    for rid, replicas in episode.rounds.items():
        for rep in replicas:
            out = audio_dir / f"{rid}.wav"
            jobs.append(TTSJob(rid, rep.model_id, rep.used_text or rep.text, str(out)))

    log(f"Synthesizing {len(jobs)} clips (offline={offline})...")
    durations = engine.run_batch(
        jobs, episodes_dir / "tts_progress.json",
        on_progress=lambda i, n, msg: log(f"TTS {i}/{n}: {msg}"),
    )
    for rid, replicas in episode.rounds.items():
        for rep in replicas:
            rep.duration_sec = durations.get(rid)
            rep.audio_path = str((audio_dir / f"{rid}.wav").resolve())
    episode.tts_files = [j.out_path for j in jobs]
    episode.status = EpisodeStatus.TTS_DONE

    # --- Timeline export (FCPXML primary + EDL/markers fallback) ---
    log("Exporting timeline (FCPXML + EDL)...")
    fps = int(config.get("timeline", "fps", default=30))
    export_res = export_timeline(episode, timeline_dir, fps=fps, sample_rate=sample_rate)
    episode.status = EpisodeStatus.EXPORTED

    # --- Script + host cues + flags ---
    log("Building script...")
    script_res = write_script(episode, script_dir)

    # --- YouTube metadata ---
    pros = config.model(episode.prosecution_model_id) or {}
    deff = config.model(episode.defense_model_id) or {}
    episode.youtube_metadata = generate_metadata(
        episode, pros.get("display_name", episode.prosecution_model_id),
        deff.get("display_name", episode.defense_model_id),
    )

    log(f"Done. Episode folder: {episodes_dir}")
    return {
        "episode_dir": str(episodes_dir),
        "audio_dir": str(audio_dir),
        "fcpxml": export_res["fcpxml"],
        "edl": export_res["edl"],
        "markers_md": export_res["markers_md"],
        "script": script_res["script"],
        "flags": script_res["flags"],
        "n_clips": len(jobs),
        "n_flags": len(episode.behaviour_flags),
    }
