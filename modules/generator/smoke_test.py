"""Smoke-test runner (Block D.3).

Generates Round 1 + the first 3 quickfire questions, runs the detector, applies
go/no-go criteria, and proposes a reframe on failure (max N=2 retries).
"""
from __future__ import annotations

import asyncio

from modules.generator.episode_generator import DEFAULT_QUICKFIRE, EpisodeGenerator, load_prompt
from modules.quickfire.variability import variability_score
from modules.schemas import Episode, Side, SmokeTestResult


async def run_smoke_test(
    gen: EpisodeGenerator,
    episode: Episode,
    thesis_variants: list[str] | None = None,
    max_retries: int = 2,
) -> SmokeTestResult:
    config = gen.config
    threshold = float(config.get("quickfire", "variability_threshold", default=0.35))
    variants = thesis_variants or []
    pros_cfg = config.model(episode.prosecution_model_id)
    def_cfg = config.model(episode.defense_model_id)

    attempt = 0
    current_thesis = episode.thesis
    while True:
        attempt += 1
        pros_sys = load_prompt("prosecution", pros_cfg["season"]).format(thesis=current_thesis)
        def_sys = load_prompt("defense", def_cfg["season"]).format(thesis=current_thesis)

        r1p, r1d = await asyncio.gather(
            gen._gen_replica(episode, episode.prosecution_model_id, Side.PROSECUTION,
                             "r1_prosecution", pros_sys, "Opening statement (Round 1).",
                             episode.gen_params.max_tokens, pros_cfg),
            gen._gen_replica(episode, episode.defense_model_id, Side.DEFENSE,
                             "r1_defense", def_sys, "Opening statement (Round 1).",
                             episode.gen_params.max_tokens, def_cfg),
        )

        qf_tasks = []
        for q in DEFAULT_QUICKFIRE[:3]:
            qu = f"Quickfire question: {q}\nAnswer in one or two sentences, take a clear stance."
            qf_tasks.append(gen._gen_replica(episode, episode.prosecution_model_id, Side.PROSECUTION,
                                             "qf_pros", pros_sys, qu, episode.gen_params.quickfire_max_tokens, pros_cfg))
            qf_tasks.append(gen._gen_replica(episode, episode.defense_model_id, Side.DEFENSE,
                                             "qf_def", def_sys, qu, episode.gen_params.quickfire_max_tokens, def_cfg))
        qf = await asyncio.gather(*qf_tasks)

        r1_flags = r1p.flags + r1d.flags
        deepseek_bad = any(
            f.flag_type in ("REFUSED", "SUPPRESSED") and "deepseek" in f.model_id.lower()
            for f in r1_flags
        )
        any_bad = any(f.flag_type in ("EVASIVE", "REFUSED") for f in r1_flags)
        variability_ok = all(
            variability_score(qf[i * 2].text, qf[i * 2 + 1].text) >= threshold
            for i in range(3)
        )
        passed = not deepseek_bad and not any_bad and variability_ok

        if passed:
            return SmokeTestResult(passed=True, attempt=attempt, flags=r1_flags,
                                   detail="go/no-go criteria met")
        if attempt > max_retries:
            return SmokeTestResult(
                passed=False, attempt=attempt, flags=r1_flags,
                detail="FAILED after retries; consider a backup topic",
                reframe=current_thesis,
            )
        # Reframe: prefer a provided thesis variant, else academic reframing.
        if attempt - 1 < len(variants):
            current_thesis = variants[attempt - 1]
        else:
            current_thesis = (
                f"From an academic, evidence-first standpoint, evaluate: {episode.thesis}"
            )
