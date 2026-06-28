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
import zipfile
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
    except RuntimeError:
        pass  # DB not initialised (tests / pre-onboarding); JSON is authoritative
    except Exception as exc:  # surface unexpected DB failures instead of hiding them (F8)
        import logging

        logging.getLogger(__name__).warning("episode DB mirror failed: %s", exc)
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
    """{done, failed, total, remaining} from the resumable TTS checkpoint.

    Counts only clips that belong to the CURRENT rounds so a stale checkpoint
    (e.g. after a quickfire re-selection dropped old r2 clips) cannot report
    more done than total (F4)."""
    state = TTSEngine._load_checkpoint(tts_checkpoint_path(episode, config))
    current = set(episode.rounds.keys())
    total = n_clips(episode)
    done = sum(1 for k, v in state.items() if v == "done" and k in current)
    failed = sum(1 for k, v in state.items() if v == "failed" and k in current)
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
    episode: Episode, config: Config, *, offline: bool, on_log: LogFn | None = None,
    on_checkpoint: Callable[[Episode], None] | None = None,
) -> EpisodeGenerator:
    orch = Orchestrator(config, offline=offline)
    return EpisodeGenerator(config, orch, on_log=on_log or (lambda m: None),
                            on_checkpoint=on_checkpoint)


# --------------------------------------------------------------------------- #
# Generation checkpoint inspection (G1; mirrors the TTS checkpoint summary)
# --------------------------------------------------------------------------- #
_CORE_ROUND_IDS = (
    ["r1_prosecution", "r1_defense"]
    + [f"r3_p{n}_{side}" for n in (1, 2, 3) for side in ("prosecution", "defense")]
    + ["r4_prosecution", "r4_defense"]
)


def generation_progress(episode: Episode) -> dict:
    """{done, expected, remaining, quickfire, complete} for resumable generation.

    Counts the 8 core rounds (R1 x2, R3 x6, R4 x2); the quickfire set is tracked
    separately. ``complete`` means every core round is present AND quickfire was
    scored, i.e. a re-run of step_generate would be a no-op (G1)."""
    done = sum(1 for rid in _CORE_ROUND_IDS
               if episode.rounds.get(rid) and episode.rounds[rid][0].text)
    expected = len(_CORE_ROUND_IDS)
    return {
        "done": done,
        "expected": expected,
        "remaining": expected - done,
        "quickfire": bool(episode.quickfire),
        "complete": done == expected and bool(episode.quickfire),
    }


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
    """Studio step 4: generate all rounds + quickfire (asyncio-parallel).

    Resumable (G1): each phase is autosaved to episode.json, so a failure
    part-way leaves the finished rounds on disk and a re-run fills only the gaps.
    """
    log = on_log or (lambda m: None)
    gen = make_generator(episode, config, offline=offline, on_log=log,
                         on_checkpoint=lambda e: save_episode(e, config))
    log("Generating rounds...")
    await gen.generate_rounds(episode)
    episode.status = EpisodeStatus.GENERATED
    return episode


async def regenerate_replica(episode: Episode, config: Config, round_id: str, *,
                             offline: bool = True, seed: int | None = None) -> Replica:
    """I1: regenerate a single round's reply, keeping the previous text as a variant."""
    gen = make_generator(episode, config, offline=offline)
    old = episode.rounds.get(round_id, [None])[0]
    rep = await gen.regenerate_round(episode, round_id, seed=seed)
    if old is not None:
        rep.variants = list(old.variants) + [old.text]
    episode.rounds[round_id] = [rep]
    return rep


async def generate_variants(episode: Episode, config: Config, round_id: str, n: int = 2, *,
                            offline: bool = True) -> list[str]:
    """I1: produce N alternative takes for a round (current text unchanged) so the
    operator can pick the best one."""
    gen = make_generator(episode, config, offline=offline)
    base = episode.gen_params.seed or 0
    texts: list[str] = []
    for i in range(max(1, n)):
        rep = await gen.regenerate_round(episode, round_id, seed=base + i + 1)
        texts.append(rep.text)
    episode.rounds[round_id][0].variants = texts
    return texts


def select_variant(episode: Episode, round_id: str, text: str) -> None:
    """I1: adopt a chosen variant as the round's reply (manual selection, ТЗ B.3)."""
    rep = episode.rounds[round_id][0]
    if rep.text != text:
        rep.variants = [t for t in ([rep.text] + rep.variants) if t != text]
    rep.text = text
    rep.used_text = text


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
    # Live mode without a key would otherwise silently degrade to silent placeholder
    # WAVs (the engine treats a missing key as offline). Fail loudly so an operator
    # who asked for real audio never ships a silent episode by accident.
    if not offline and not el_key:
        raise ValueError(
            "Live TTS requested but ELEVENLABS_API_KEY is not set. "
            "Add the key (Config screen / .env) or run this step in offline mode."
        )
    engine = TTSEngine(
        _presets(config), el_key, offline=offline,
        output_format=config.tts_defaults.get("output_format", "pcm_44100"),
        sample_rate=sample_rate,
    )
    jobs: list[TTSJob] = []
    for rid, replicas in episode.rounds.items():
        # Invariant: exactly one replica per round_id (clip_id and {rid}.wav are
        # keyed by rid). Fail fast instead of silently overwriting a file (F5).
        if len(replicas) != 1:
            raise ValueError(f"round '{rid}' must have exactly one replica, got {len(replicas)}")
        rep = replicas[0]
        out = audio_dir / f"{rid}.wav"
        jobs.append(TTSJob(rid, rep.model_id, rep.used_text or rep.text, str(out)))

    max_parallel = int(config.tts_defaults.get("batch_max_parallel", 1) or 1)
    durations = engine.run_batch(
        jobs, d / "tts_progress.json", on_progress=on_progress, max_parallel=max_parallel,
    )
    for rid, replicas in episode.rounds.items():
        for rep in replicas:
            rep.duration_sec = durations.get(rid)
            rep.audio_path = str((audio_dir / f"{rid}.wav").resolve())
    episode.tts_files = [j.out_path for j in jobs]
    episode.status = EpisodeStatus.TTS_DONE
    return durations


def step_export(episode: Episode, config: Config) -> dict:
    """Studio step 9: FCPXML (primary) + EDL + markers (+ optional OTIO) from state."""
    d = episode_dir(episode, config)
    fps = int(config.get("timeline", "fps", default=30))
    sample_rate = int(config.get("timeline", "audio_sample_rate", default=44100))
    emit_otio = bool(config.get("timeline", "emit_otio", default=False))
    res = export_timeline(episode, d / "timeline", fps=fps, sample_rate=sample_rate,
                          emit_otio=emit_otio)
    episode.status = EpisodeStatus.EXPORTED
    return res


def _handoff_readme(episode: Episode) -> str:
    return (
        f"# {episode.slug} — editor handoff\n\n"
        f"Motion: {episode.thesis}\n\n"
        "## Timeline\n"
        "- Import `timeline/<slug>.fcpxml` (primary) into DaVinci Resolve 19. If it does\n"
        "  not import cleanly, use `timeline/<slug>.edl` + `<slug>_markers.md`, or\n"
        "  `timeline/<slug>.otio` (native import).\n"
        "- 5 audio tracks expected: HOST_VOICE (placeholder), PROSECUTION, DEFENSE,\n"
        "  SFX_MARKERS, MUSIC_BED.\n\n"
        "## Audio\n- `audio/*.wav` — one clip per round/reply, named by round id.\n\n"
        "## Script\n- `script/episode_script.md` — full script with flags + timecodes.\n"
        "- `script/host_cues.md` — host voice-over cues.\n\n"
        "## Markers legend\n"
        "OBJECTION_SUSTAINED / OBJECTION_OVERRULED, REFUSED, SUPPRESSED, EVASIVE, WEAK,\n"
        "ROUND_START_N, POINT_N.\n"
    )


def export_bundle(episode: Episode, config: Config) -> str:
    """I9: zip the episode's audio/script/timeline + a handoff README for the editor.
    Returns the path to the created .zip in the exports dir."""
    d = episode_dir(episode, config)
    exports = config.resolve_path("exports_dir")
    exports.mkdir(parents=True, exist_ok=True)
    (d / "HANDOFF.md").write_text(_handoff_readme(episode), encoding="utf-8")

    zip_path = exports / f"{episode.slug}_pack.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for sub in ("audio", "script", "timeline"):
            base = d / sub
            if base.exists():
                for f in sorted(base.rglob("*")):
                    if f.is_file():
                        zf.write(f, arcname=str(f.relative_to(d)))
        for top in ("HANDOFF.md", "episode.json"):
            f = d / top
            if f.exists():
                zf.write(f, arcname=top)
    return str(zip_path)


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


async def run_batch_episodes(episodes: list[Episode], config: Config, *,
                             offline: bool = True, on_log: LogFn | None = None) -> list[dict]:
    """I7: run the full pipeline for several episodes sequentially (unattended).
    One failing episode is recorded and the batch continues."""
    log = on_log or (lambda m: None)
    results: list[dict] = []
    for i, ep in enumerate(episodes, 1):
        log(f"[batch {i}/{len(episodes)}] {ep.slug} ...")
        try:
            res = await run_full_pipeline(ep, config, offline=offline, on_log=log)
            save_episode(ep, config)
            results.append({"slug": ep.slug, "ok": True,
                            "episode_dir": res["episode_dir"], "n_clips": res["n_clips"]})
        except Exception as exc:
            log(f"[batch {i}] FAILED {ep.slug}: {exc}")
            results.append({"slug": ep.slug, "ok": False, "error": str(exc)})
    return results
