"""Cost & token economics (I6).

Pre-generation estimate from the fixed episode structure + generation params, and
actual cost reconstructed from the per-run usage logged by the orchestrator.
Pricing lives in config.yaml (``pricing`` section). Offline runs have no real
token usage, so actuals show 0 for LLM (TTS is char-based and still computed).
"""
from __future__ import annotations

import json

from modules.config import Config
from modules.schemas import Episode

_CHARS_PER_TOKEN = 4          # rough average for English
_INPUT_TOKENS_MAIN = 320      # system+user prompt for a full round
_INPUT_TOKENS_QF = 80         # system+user prompt for a quickfire question
_MAIN_CALLS_PER_SIDE = 5      # R1(1) + R3(3) + R4(1)


def _model_pricing(config: Config, model_id: str) -> dict:
    pricing = config.get("pricing", default={}) or {}
    per = (pricing.get("per_model") or {}).get(model_id)
    return per or pricing.get("default") or {"input_per_mtok": 0.0, "output_per_mtok": 0.0}


def _tts_per_mchar(config: Config) -> float:
    return float((config.get("pricing", default={}) or {}).get("tts_per_mchar", 0.0))


def estimate_episode_cost(episode: Episode, config: Config) -> dict:
    """USD estimate before generation, from the fixed structure + gen params."""
    gp = episode.gen_params
    n_qf = int(config.get("quickfire", "questions_generated", default=12))
    sel = int(config.get("quickfire", "questions_selected", default=10))

    in_tokens = out_tokens = 0
    llm = 0.0
    for mid in (episode.prosecution_model_id, episode.defense_model_id):
        pr = _model_pricing(config, mid)
        side_in = _MAIN_CALLS_PER_SIDE * _INPUT_TOKENS_MAIN + n_qf * _INPUT_TOKENS_QF
        side_out = _MAIN_CALLS_PER_SIDE * gp.max_tokens + n_qf * gp.quickfire_max_tokens
        in_tokens += side_in
        out_tokens += side_out
        llm += side_in / 1e6 * pr["input_per_mtok"] + side_out / 1e6 * pr["output_per_mtok"]

    # TTS bills the spoken clips: both sides' main rounds + the selected quickfire.
    tts_tokens = _MAIN_CALLS_PER_SIDE * 2 * gp.max_tokens + sel * 2 * gp.quickfire_max_tokens
    tts = (tts_tokens * _CHARS_PER_TOKEN) / 1e6 * _tts_per_mchar(config)

    return {
        "llm_usd": round(llm, 4),
        "tts_usd": round(tts, 4),
        "total_usd": round(llm + tts, 4),
        "est_input_tokens": in_tokens,
        "est_output_tokens": out_tokens,
    }


def within_budget(episode: Episode, config: Config) -> tuple[bool, float, float | None]:
    """G2: pre-flight the estimated episode cost against an optional spend cap.

    Returns ``(ok, est_total_usd, cap_usd)``. ``pricing.max_episode_usd = null``
    (or absent) means no limit -> always ok. The UI uses this to block or confirm
    a live run before any paid API call is made."""
    cap_raw = (config.get("pricing", default={}) or {}).get("max_episode_usd")
    est = float(estimate_episode_cost(episode, config)["total_usd"])
    cap = None if cap_raw is None else float(cap_raw)
    return (cap is None or est <= cap), est, cap


def tts_cost(episode: Episode, config: Config) -> float:
    """Actual TTS cost from the characters that were (or will be) spoken."""
    chars = sum(len(rep.used_text or rep.text)
                for reps in episode.rounds.values() for rep in reps)
    return round(chars / 1e6 * _tts_per_mchar(config), 4)


def episode_cost_from_logs(episode: Episode, config: Config) -> dict:
    """Actual USD from the logged per-run usage (input/output tokens per model)."""
    logs_dir = config.resolve_path("logs_dir") / str(episode.id)
    by_model: dict[str, dict[str, int]] = {}
    if logs_dir.exists():
        for p in logs_dir.glob("*.jsonl"):
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                usage = (rec.get("result") or {}).get("usage") or {}
                mid = rec.get("model_id", "?")
                it = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
                ot = usage.get("output_tokens") or usage.get("completion_tokens") or 0
                d = by_model.setdefault(mid, {"input": 0, "output": 0})
                d["input"] += int(it or 0)
                d["output"] += int(ot or 0)

    llm = 0.0
    for mid, d in by_model.items():
        pr = _model_pricing(config, mid)
        llm += d["input"] / 1e6 * pr["input_per_mtok"] + d["output"] / 1e6 * pr["output_per_mtok"]
    tts = tts_cost(episode, config)
    return {
        "llm_usd": round(llm, 4),
        "tts_usd": round(tts, 4),
        "total_usd": round(llm + tts, 4),
        "by_model": by_model,
    }
