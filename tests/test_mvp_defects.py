"""MVP-audit remediation tests (D1-D3).

D1: live TTS without an ElevenLabs key fails loudly instead of silently
    shipping silent placeholder WAVs.
D2: TTSEngine.run_batch honours ``batch_max_parallel`` -- it synthesizes
    clips concurrently, still resumes/skips done clips, and keeps fail-fast.
D3: the production-pair defense model (claude-sonnet-4-6) has its own pricing
    so cost math does not silently fall back to the generic default.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from modules.config import load_config
from modules.economics import _model_pricing
from modules.episodes import manager as mgr
from modules.schemas import Episode
from modules.tts.engine import TTSEngine, TTSJob, _write_silence_wav


def _cfg(tmp_path):
    config = load_config()
    config._data["app"]["episodes_dir"] = str(tmp_path / "episodes")
    config._data["app"]["logs_dir"] = str(tmp_path / "logs")
    return config


def _ep(slug: str) -> Episode:
    return Episode(thesis="The dissolution of the USSR was inevitable.", slug=slug,
                   prosecution_model_id="gpt-5.5", defense_model_id="claude-sonnet-4-6")


def _jobs(tmp_path, n: int, text: str = "one two three") -> list[TTSJob]:
    return [TTSJob(clip_id=f"c{i}", llm_model_id="x", text=text,
                   out_path=str(tmp_path / f"c{i}.wav")) for i in range(n)]


# --- D1: live TTS without a key must raise, not degrade silently -----------
def test_d1_live_tts_without_key_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "get_secret", lambda name: None)  # no key in env
    config = _cfg(tmp_path)
    ep = _ep("ep_d1")
    with pytest.raises(ValueError, match="ELEVENLABS_API_KEY"):
        mgr.step_tts(ep, config, offline=False)


def test_d1_offline_still_allowed_without_key(tmp_path, monkeypatch):
    # The loud failure must be scoped to live mode only -- offline keeps working.
    monkeypatch.setattr(mgr, "get_secret", lambda name: None)
    config = _cfg(tmp_path)
    ep = _ep("ep_d1_off")
    import asyncio

    asyncio.run(mgr.step_generate(ep, config, offline=True))
    durations = mgr.step_tts(ep, config, offline=True)
    assert durations and all(v > 0 for v in durations.values())


# --- D2: run_batch is bounded-parallel ------------------------------------
def test_d2_parallel_batch_completes_all(tmp_path):
    engine = TTSEngine(presets={}, api_key=None, offline=True)
    jobs = _jobs(tmp_path, 8)
    ckpt = tmp_path / "ckpt.json"
    durations = engine.run_batch(jobs, ckpt, max_parallel=4)

    assert len(durations) == 8
    assert all(Path(j.out_path).exists() for j in jobs)
    state = json.loads(ckpt.read_text(encoding="utf-8"))
    assert all(state[f"c{i}"] == "done" for i in range(8))


def test_d2_runs_jobs_concurrently(tmp_path):
    """Proves max_parallel actually overlaps work (was always serial before)."""
    counter = {"active": 0, "peak": 0}
    lock = threading.Lock()

    class _Engine(TTSEngine):
        def generate_one(self, job: TTSJob) -> float:
            with lock:
                counter["active"] += 1
                counter["peak"] = max(counter["peak"], counter["active"])
            time.sleep(0.05)
            with lock:
                counter["active"] -= 1
            _write_silence_wav(job.out_path, 1.0)
            return 1.0

    engine = _Engine(presets={}, api_key=None, offline=True)
    engine.run_batch(_jobs(tmp_path, 6), tmp_path / "ckpt.json", max_parallel=3)
    assert counter["peak"] >= 2  # serial code could never exceed 1


def test_d2_failure_propagates_and_checkpoints(tmp_path):
    class _Engine(TTSEngine):
        def generate_one(self, job: TTSJob) -> float:
            if job.clip_id == "bad":
                raise RuntimeError("boom")
            _write_silence_wav(job.out_path, 1.0)
            return 1.0

    engine = _Engine(presets={}, api_key=None, offline=True)
    jobs = [
        TTSJob("ok1", "x", "t", str(tmp_path / "ok1.wav")),
        TTSJob("bad", "x", "t", str(tmp_path / "bad.wav")),
        TTSJob("ok2", "x", "t", str(tmp_path / "ok2.wav")),
    ]
    ckpt = tmp_path / "ckpt.json"
    with pytest.raises(RuntimeError, match="boom"):
        engine.run_batch(jobs, ckpt, max_parallel=2)
    state = json.loads(ckpt.read_text(encoding="utf-8"))
    assert state.get("bad") == "failed"


def test_d2_resume_skips_done_in_parallel(tmp_path):
    # A clip already 'done' on disk must be measured (F1), never re-synthesized,
    # even on the parallel path.
    done_wav = tmp_path / "c0.wav"
    _write_silence_wav(done_wav, 2.5, 44100)
    ckpt = tmp_path / "ckpt.json"
    ckpt.write_text(json.dumps({"c0": "done"}), encoding="utf-8")

    regen = {"hit": False}

    class _Engine(TTSEngine):
        def generate_one(self, job: TTSJob) -> float:
            if job.clip_id == "c0":
                regen["hit"] = True
            return super().generate_one(job)

    engine = _Engine(presets={}, api_key=None, offline=True)
    jobs = [
        TTSJob("c0", "x", "one two three four five", str(done_wav)),
        TTSJob("c1", "x", "one two three", str(tmp_path / "c1.wav")),
    ]
    durations = engine.run_batch(jobs, ckpt, max_parallel=2)
    assert regen["hit"] is False                 # resumed, not regenerated
    assert abs(durations["c0"] - 2.5) < 0.05     # measured the real WAV (F1)
    assert "c1" in durations


def test_d2_step_tts_passes_config_parallelism(tmp_path, monkeypatch):
    # The Studio step must thread batch_max_parallel through to the engine.
    captured: dict = {}
    real = TTSEngine.run_batch

    def _spy(self, jobs, ckpt, on_progress=None, *, max_parallel=1):
        captured["max_parallel"] = max_parallel
        return real(self, jobs, ckpt, on_progress=on_progress, max_parallel=max_parallel)

    monkeypatch.setattr(TTSEngine, "run_batch", _spy)
    config = _cfg(tmp_path)
    config._data["tts_defaults"]["batch_max_parallel"] = 5
    ep = _ep("ep_d2_cfg")
    import asyncio

    asyncio.run(mgr.step_generate(ep, config, offline=True))
    mgr.step_tts(ep, config, offline=True)
    assert captured["max_parallel"] == 5


# --- D3: production-pair defense model has explicit pricing ----------------
def test_d3_sonnet_pricing_present():
    config = load_config()
    pr = _model_pricing(config, "claude-sonnet-4-6")
    assert pr["input_per_mtok"] == 3.0
    assert pr["output_per_mtok"] == 15.0
    # Must differ from the generic default -> proves the per-model entry is used.
    assert pr != _model_pricing(config, "totally-unknown-model-xyz")
