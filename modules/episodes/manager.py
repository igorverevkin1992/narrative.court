"""Episode Manager (Module 12).

Drives the episode pipeline and owns episode persistence. The pipeline is split
into composable, re-runnable *step* functions so the Episode Studio UI can drive
it one operator-controlled step at a time (Block L, 10-step flow), while
``run_full_pipeline`` keeps the one-shot offline path used by the MVP and tests.

Step functions (each advances the status machine and is safe to re-run):
    step_smoke_test -> step_generate -> step_tts -> step_export -> step_script
    -> step_metadata

Persistence: ``save_episode``/``load_episode`` write ``episode.json`` into the
episode folder, giving the UI autosave + resume (Block L.2, O.2).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from modules.config import ROOT, Config, get_secret
from modules.generator.episode_generator import EpisodeGenerator
from modules.generator.smoke_test import run_smoke_test
from modules.llm.orchestrator import Orchestrator
from modules.metadata.generator import generate_metadata
from modules.schemas import Episode, EpisodeStatus, Replica, Side, SmokeTestResult
from modules.script.builder import write_script
from modules.timeline.exporter import export_timeline
from modules.tts.engine import TTSEngine, TTSJob
from modules.tts.presets import load_presets

LogFn = Callable[[str], None]
ProgressFn = Callable[[int, int, str], None]


# --------------------------------------------------------------------------- #
# Paths + persistence
# --------------------------------------------------------------------------- #
def episode_dir(episode: Episode, config: Config) -> Path:
    """Absolute path to this episode's folder (data/episodes/<slug>/)."""
    return config.resolve_path("episodes_dir") / episode.slug


def _presets(config: Config) -> dict:
    presets_rel = config.tts_defaults.get("presets_file", "./config/tts_presets.json")
    presets_path = Path(presets_rel)
    if not presets_path.is_absolute():
        presets_path = ROOT / presets_rel.lstrip("./")
    return load_presets(presets_path)


def save_episode(episode: Episode, config: Config) -> Path:
    """Atomically autosave the episode JSON (tmp + os.replace) so a crash mid-write
    never corrupts the resume file. Best-effort mirror into the SQLite episodes
    table for Planner/Dashboard; JSON remains the source of truth."""
    d = episode_dir(episode, config)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "episode.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(episode.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomic on POSIX and Windows
    try:
        from modules.db import save_episode as _db_save_episode

        _db_save_episode(episode)
    except Exception:
        pass  # DB optional (not initialised in tests; JSON is authoritative)
    return path


def load_episode(path: str | Path) -> Episode:
    """Load an episode from a saved ``episode.json``."""
    return Episode.model_validate_json(Path(path).read_text(encoding="utf-8"))


def list_saved_episodes(config: Config) -> list[Episode]:
    """Scan data/episodes/*/episode.json for resumable drafts (newest first)."""
    base = config.resolve_path("episodes_dir")
    out: list[Episode] = []
    if not base.exists():
        return out
    for p in sorted(base.glob("*/episode.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            out.append(load_episode(p))
        except Exception:
            continue
    return out


def n_clips(episode: Episode) -> int:
    """Total number of replica clips currently on the episode."""
    return sum(len(reps) for reps in episode.rounds.values())


# --------------------------------------------------------------------------- #
# TTS checkpoint inspection (Block O.2)
# --------------------------------------------------------------------------- #
def tts_checkpoint_path(episode: Episode, config: Config) -> Path:
    return episode_dir(episode, config) / "tts_progress.json"


def tts_checkpoint_summary(episode: Episode, config: Config) -> dict:
    """{done, failed, total, remaining} from the resumable TTS checkpoint."""
    state = TTSEngine._load_checkpoint(tts_checkpoint_path(episode, config))
    total = n_clips(episode)
    done = sum(1 for v in state.values() if v == "done")
    failed = sum(1 for v in state.values() if v == "failed")
    return {"done": done, "failed": failed, "total": total, "remaining": max(0, total - done)}


def reset_tts_checkpoint(episode: Episode, config: Config) -> bool:
    """Clear the checkpoint so the next TTS run regenerates every clip. Returns
    True if a checkpoint existed."""
    p = tts_checkpoint_path(episode, config)
    if p.exists():
        p.unlink()
        return True
    return False


# --------------------------------------------------------------------------- #
# Generator / orchestrator construction
# --------------------------------------------------------------------------- #
def make_generator(
    episode: Episode, config: Config, *, offline: bool, on_log: LogFn | None = None
) -> EpisodeGenerator:
    orch = Orchestrator(config, offline=offline)
    return EpisodeGenerator(config, orch, on_log=on_log or (lambda m: None))


# --------------------------------------------------------------------------- #
# Quickfire selection (re-syncs the r2_* replicas to operator's chosen set)
# --------------------------------------------------------------------------- #
def sync_quickfire_selection(episode: Episode) -> int:
    """Rebuild the r2_q* round replicas from ``episode.quickfire`` recommendations.

    Called after the operator overrides the auto-selection in Studio step 8 so
    that TTS + timeline only include the chosen exchanges. Returns the count of
    selected exchanges.
    """
    for key in [k for k in episode.rounds if k.startswith("r2_q")]:
        del episode.rounds[key]
    selected = [ex for ex in episode.quickfire if ex.recommended]
    for i, ex in enumerate(selected, 1):
        episode.rounds[f"r2_q{i:02d}_prosecution"] = [Replica(
            round_id=f"r2_q{i:02d}_prosecution", side=Side.PROSECUTION,
            model_id=episode.prosecution_model_id, text=ex.prosecution_answer,
            used_text=ex.prosecution_answer)]
        episode.rounds[f"r2_q{i:02d}_defense"] = [Replica(
            round_id=f"r2_q{i:02d}_defense", side=Side.DEFENSE,
            model_id=episode.defense_model_id, text=ex.defense_answer,
            used_text=ex.defense_answer)]
    return len(selected)


# --------------------------------------------------------------------------- #
# Pipeline steps
# --------------------------------------------------------------------------- #
async def step_smoke_test(
    episode: Episode,
    config: Config,
    *,
    offline: bool = True,
    thesis_variants: list[str] | None = None,
    on_log: LogFn | None = None,
) -> SmokeTestResult:
    """Studio step 3: Round-1 + 3 quickfire go/no-go with reframe on failure."""
    gen = make_generator(episode, config, offline=offline, on_log=on_log)
    max_retries = int(config.get("smoke_test", "max_retries", default=2))
    result = await run_smoke_test(
        gen, episode, thesis_variants=thesis_variants, max_retries=max_retries
    )
    if result.passed:
        episode.status = EpisodeStatus.SMOKE_TESTED
    return result


async def step_generate(
    episode: Episode, config: Config, *, offline: bool = True, on_log: LogFn | None = None
) -> Episode:
    """Studio step 4: generate all rounds + quickfire (asyncio-parallel)."""
    log = on_log or (lambda m: None)
    gen = make_generator(episode, config, offline=offline, on_log=log)
    log("Generating rounds...")
    await gen.generate_rounds(episode)
    episode.status = EpisodeStatus.GENERATED
    return episode


def step_tts(
    episode: Episode,
    config: Config,
    *,
    offline: bool = True,
    on_progress: ProgressFn | None = None,
) -> dict[str, float]:
    """Studio step 7: synthesize one WAV per replica (resumable batch, Block O.2)."""
    d = episode_dir(episode, config)
    audio_dir = d / "audio"
    sample_rate = int(config.get("timeline", "audio_sample_rate", default=44100))
    el_key = None if offline else get_secret("ELEVENLABS_API_KEY")
    engine = TTSEngine(
        _presets(config), el_key, offline=offline,
        output_format=config.tts_defaults.get("output_format", "pcm_44100"),
        sample_rate=sample_rate,
    )
    jobs: list[TTSJob] = []
    for rid, replicas in episode.rounds.items():
        for rep in replicas:
            out = audio_dir / f"{rid}.wav"
            jobs.append(TTSJob(rid, rep.model_id, rep.used_text or rep.text, str(out)))

    durations = engine.run_batch(jobs, d / "tts_progress.json", on_progress=on_progress)
    for rid, replicas in episode.rounds.items():
        for rep in replicas:
            rep.duration_sec = durations.get(rid)
            rep.audio_path = str((audio_dir / f"{rid}.wav").resolve())
    episode.tts_files = [j.out_path for j in jobs]
    episode.status = EpisodeStatus.TTS_DONE
    return durations


def step_export(episode: Episode, config: Config) -> dict:
    """Studio step 9: FCPXML (primary) + EDL + markers from current state."""
    d = episode_dir(episode, config)
    fps = int(config.get("timeline", "fps", default=30))
    sample_rate = int(config.get("timeline", "audio_sample_rate", default=44100))
    res = export_timeline(episode, d / "timeline", fps=fps, sample_rate=sample_rate)
    episode.status = EpisodeStatus.EXPORTED
    return res


def step_script(episode: Episode, config: Config) -> dict:
    """Build episode_script.md + host_cues.md + behaviour_flags.json."""
    return write_script(episode, episode_dir(episode, config) / "script")


def step_metadata(episode: Episode, config: Config) -> dict:
    """Studio step 10: YouTube title/description/tags/timestamps."""
    pros = config.model(episode.prosecution_model_id) or {}
    deff = config.model(episode.defense_model_id) or {}
    episode.youtube_metadata = generate_metadata(
        episode,
        pros.get("display_name", episode.prosecution_model_id),
        deff.get("display_name", episode.defense_model_id),
    )
    return episode.youtube_metadata


# --------------------------------------------------------------------------- #
# One-shot pipeline (MVP + tests) -- composition of the steps above
# --------------------------------------------------------------------------- #
async def run_full_pipeline(
    episode: Episode,
    config: Config,
    *,
    offline: bool = True,
    on_log: LogFn | None = None,
) -> dict:
    """Run the entire pipeline end-to-end (offline-capable). Used by the MVP
    one-click button and the pipeline tests; returns artifact paths + counts."""
    log = on_log or (lambda m: None)

    await step_generate(episode, config, offline=offline, on_log=log)

    log(f"Synthesizing {n_clips(episode)} clips (offline={offline})...")
    step_tts(
        episode, config, offline=offline,
        on_progress=lambda i, n, msg: log(f"TTS {i}/{n}: {msg}"),
    )

    log("Exporting timeline (FCPXML + EDL)...")
    export_res = step_export(episode, config)

    log("Building script...")
    script_res = step_script(episode, config)

    step_metadata(episode, config)

    d = episode_dir(episode, config)
    log(f"Done. Episode folder: {d}")
    return {
        "episode_dir": str(d),
        "audio_dir": str(d / "audio"),
        "fcpxml": export_res["fcpxml"],
        "edl": export_res["edl"],
        "markers_md": export_res["markers_md"],
        "script": script_res["script"],
        "flags": script_res["flags"],
        "n_clips": n_clips(episode),
        "n_flags": len(episode.behaviour_flags),
    }
