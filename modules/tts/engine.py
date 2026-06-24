"""TTS Engine (Module 6).

ElevenLabs non-streaming convert -> WAV per replica, with a resumable batch
checkpoint (Block O.2). In offline mode (no key / offline=True) it writes a
silent placeholder WAV of the estimated duration so the timeline still builds.
"""
from __future__ import annotations

import json
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from modules.schemas import TTSPreset

WORDS_PER_SECOND = 2.6  # ~155 wpm broadcast pace


@dataclass
class TTSJob:
    clip_id: str
    llm_model_id: str
    text: str
    out_path: str


def estimate_duration(text: str) -> float:
    words = max(1, len(text.split()))
    return max(0.5, round(words / WORDS_PER_SECOND, 3))


def _write_silence_wav(path: str | Path, seconds: float, sample_rate: int = 44100) -> None:
    n_frames = int(seconds * sample_rate)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # PCM 16-bit
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * n_frames)


class TTSEngine:
    def __init__(
        self,
        presets: dict[str, TTSPreset],
        api_key: str | None = None,
        *,
        offline: bool = False,
        output_format: str = "pcm_44100",
        sample_rate: int = 44100,
    ):
        self.presets = presets
        self.api_key = api_key
        self.offline = offline or not api_key
        self.output_format = output_format
        self.sample_rate = sample_rate

    # -- checkpoint ---------------------------------------------------------
    @staticmethod
    def _load_checkpoint(path: Path) -> dict:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    @staticmethod
    def _save_checkpoint(path: Path, data: dict) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)  # atomic

    # -- single job ---------------------------------------------------------
    def generate_one(self, job: TTSJob) -> float:
        """Synthesize one clip; return its duration in seconds."""
        if self.offline:
            dur = estimate_duration(job.text)
            _write_silence_wav(job.out_path, dur, self.sample_rate)
            return dur

        preset = self.presets.get(job.llm_model_id)
        if preset is None:
            raise ValueError(f"No TTS preset for model {job.llm_model_id}")
        try:
            from elevenlabs import VoiceSettings  # type: ignore
            from elevenlabs.client import ElevenLabs  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("elevenlabs SDK not installed") from exc

        client = ElevenLabs(api_key=self.api_key)
        audio = client.text_to_speech.convert(
            voice_id=preset.el_voice_id,
            model_id=preset.el_model_id,
            text=job.text,
            output_format=self.output_format,
            voice_settings=VoiceSettings(
                stability=preset.stability,
                similarity_boost=preset.similarity_boost,
                style=preset.style,
                use_speaker_boost=preset.use_speaker_boost,
            ),
        )
        pcm = b"".join(audio) if hasattr(audio, "__iter__") else bytes(audio)
        self._write_pcm_wav(job.out_path, pcm, self.sample_rate)
        from modules.timeline.timecode_calculator import audio_duration_sec

        return audio_duration_sec(job.out_path)

    @staticmethod
    def _write_pcm_wav(path: str | Path, pcm_bytes: bytes, sample_rate: int) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm_bytes)

    # -- batch with resume --------------------------------------------------
    def run_batch(
        self,
        jobs: list[TTSJob],
        checkpoint_path: str | Path,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> dict[str, float]:
        """Run all jobs; skip ones already 'done'. Returns clip_id -> duration."""
        checkpoint_path = Path(checkpoint_path)
        state = self._load_checkpoint(checkpoint_path)
        durations: dict[str, float] = {}
        total = len(jobs)

        for i, job in enumerate(jobs, 1):
            entry = state.get(job.clip_id)
            if entry == "done" and Path(job.out_path).exists():
                durations[job.clip_id] = estimate_duration(job.text)
                if on_progress:
                    on_progress(i, total, f"skip {job.clip_id} (done)")
                continue
            try:
                dur = self.generate_one(job)
                durations[job.clip_id] = dur
                state[job.clip_id] = "done"
            except Exception as exc:
                state[job.clip_id] = "failed"
                self._save_checkpoint(checkpoint_path, state)
                if on_progress:
                    on_progress(i, total, f"FAILED {job.clip_id}: {exc}")
                raise
            self._save_checkpoint(checkpoint_path, state)
            if on_progress:
                on_progress(i, total, f"done {job.clip_id}")
        return durations
