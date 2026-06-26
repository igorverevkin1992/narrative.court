"""Episode Generator (Module 3).

Generates all rounds with asyncio parallelism, injects Round 1 opponent text into
Round 3, runs the Behaviour Detector on every reply, applies the translation
layer to Season-2 models, and scores/selects the quickfire.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Callable

from modules.config import Config
from modules.detector.behaviour_detector import detect
from modules.llm.orchestrator import Orchestrator
from modules.quickfire.manager import WORD_BUDGET, score_and_select, trim_to_words
from modules.schemas import Episode, QuickfireExchange, Replica, Side
from modules.translation.gemini_corrector import correct

PROMPTS_DIR = Path(__file__).parent / "prompts"

DEFAULT_QUICKFIRE = [
    "Was the outcome inevitable or contingent?",
    "Name the single most decisive cause.",
    "Did economic or political factors matter more?",
    "Was there a viable alternative path?",
    "Did leadership change the outcome?",
    "Was external pressure decisive?",
    "Would a different decade have changed things?",
    "Is the consensus view correct?",
    "Did institutions or individuals drive events?",
    "Was collapse a failure or a transformation?",
    "Could reform have succeeded?",
    "Is the motion a matter of fact or judgment?",
]


def load_prompt(side: str, season: int) -> str:
    return (PROMPTS_DIR / f"{side}_s{season}.txt").read_text(encoding="utf-8")


# Control delimiters that frame injected text in prompts. Strip them from any
# model-generated text we inject so a model cannot break out of the block (F6).
_CONTROL_DELIMS = re.compile(r"\[/?(?:OPPONENT_OPENING_STATEMENT|YOUR_REBUTTALS)\]")


def _strip_delims(text: str) -> str:
    return _CONTROL_DELIMS.sub("", text)


def _r3_user(side: str, n: int, opponent_text: str) -> str:
    opponent_text = _strip_delims(opponent_text)
    return (
        f"[OPPONENT_OPENING_STATEMENT]\n{opponent_text}\n[/OPPONENT_OPENING_STATEMENT]\n\n"
        f"You are arguing {side}. Rebut Point {n} of the opponent's opening above. "
        f"Attack the weakest factual claim. Do not concede your position."
    )


def _maybe_translate(orch: Orchestrator, config: Config, text: str, needs_translation: bool):
    if not needs_translation or orch.offline:
        return correct(text, needs_translation=False)
    corrector_id = config.get("translation_corrector", "model_id", default="gemini-3.1-pro")

    def fn(system_prompt: str, t: str) -> str:
        res = orch.generate(corrector_id, system_prompt, t, 0.0, max(64, int(len(t) * 1.3)))
        return res.content

    threshold = float(config.get("translation", "drift_threshold", default=0.70))
    method = config.get("translation", "drift_method", default="keyword_overlap")
    judge = None
    if method == "llm_judge":
        def judge(orig: str, corr: str) -> float:
            system = ("On a 0-10 scale, how much did the EDIT change the meaning or facts "
                      "vs the ORIGINAL (10 = heavily changed)? Reply with ONLY the number.")
            r = orch.generate(corrector_id, system, f"ORIGINAL: {orig}\nEDIT: {corr}", 0.0, 8)
            m = re.search(r"\d+(?:\.\d+)?", r.content or "")
            return float(m.group()) / 10.0 if m else 0.0
    return correct(text, needs_translation=True, corrector_fn=fn, threshold=threshold,
                   drift_method=method, drift_judge_fn=judge)


class EpisodeGenerator:
    def __init__(self, config: Config, orchestrator: Orchestrator,
                 on_log: Callable[[str], None] | None = None):
        self.config = config
        self.orch = orchestrator
        self.on_log = on_log or (lambda msg: None)
        self.det = config.get("detector", default={}) or {}

    async def _gen_replica(self, episode, model_id, side, round_id, system, user,
                           max_tokens, model_cfg, *, seed_override: int | None = None) -> Replica:
        seed = episode.gen_params.seed if seed_override is None else seed_override
        result = await self.orch.agenerate(
            model_id, system, user, episode.gen_params.temperature, max_tokens,
            seed, episode_id=episode.id, round_id=round_id,
        )
        needs_tr = bool(model_cfg.get("translation_layer"))
        translation = _maybe_translate(self.orch, self.config, result.content, needs_tr)

        flags = detect(
            result, model_id, round_id,
            reasoning_min_chars=int(self.det.get("deepseek_reasoning_min_chars", 80)),
            content_min_chars=int(self.det.get("deepseek_content_min_chars", 200)),
            evasive_threshold=int(self.det.get("hedging_evasive_threshold", 3)),
            weak_threshold=int(self.det.get("hedging_weak_threshold", 1)),
        )
        self.on_log(f"[{round_id}] {model_id}: {len(result.content)} chars, {len(flags)} flag(s)")
        return Replica(
            round_id=round_id, side=side, model_id=model_id, text=result.content,
            used_text=translation.used_text, flags=flags,
        )

    def _variability_judge(self):
        """LLM-judge rating opposition 0..1 (Block E.1 llm_judge, I2); None offline."""
        if self.orch.offline:
            return None
        judge_id = self.config.get("translation_corrector", "model_id", default="gemini-3.1-pro")

        def judge(a: str, b: str) -> float:
            system = ("Rate how OPPOSED two debate answers are on a 0-10 integer scale "
                      "(10 = directly contradictory). Reply with ONLY the number.")
            res = self.orch.generate(judge_id, system, f"A: {a}\nB: {b}", 0.0, 8)
            m = re.search(r"\d+(?:\.\d+)?", res.content or "")
            return float(m.group()) / 10.0 if m else 0.0

        return judge

    def _prompt_for_round(self, episode, round_id):
        """Reconstruct (model_id, side, model_cfg, system, user, max_tokens) for a
        single round so it can be regenerated in isolation (I1)."""
        side = Side.PROSECUTION if round_id.endswith("prosecution") else Side.DEFENSE
        model_id = (episode.prosecution_model_id if side == Side.PROSECUTION
                    else episode.defense_model_id)
        cfg = self.config.model(model_id)
        system = load_prompt(side.value, cfg["season"]).replace("{thesis}", episode.thesis)
        mt = episode.gen_params.max_tokens
        if round_id.startswith("r1_"):
            user = "Deliver your opening statement (Round 1) arguing your assigned side."
        elif round_id.startswith("r3_"):
            n = int(round_id.split("_")[1][1:])
            opp_key = "r1_defense" if side == Side.PROSECUTION else "r1_prosecution"
            user = _r3_user(side.value, n, episode.rounds[opp_key][0].text)
        elif round_id.startswith("r4_"):
            summ = " ".join(episode.rounds[f"r3_p{k}_{side.value}"][0].text for k in (1, 2, 3))
            user = (f"[YOUR_REBUTTALS]\n{_strip_delims(summ)}\n[/YOUR_REBUTTALS]\n\n"
                    "Deliver your closing statement (Round 4). Summarize why your side prevailed.")
        else:  # quickfire / other
            mt = episode.gen_params.quickfire_max_tokens
            user = "Quickfire question: answer in one or two sentences, taking a clear stance."
        return model_id, side, cfg, system, user, mt

    async def regenerate_round(self, episode, round_id, *, seed: int | None = None) -> Replica:
        """Generate a fresh take for a single round (I1)."""
        model_id, side, cfg, system, user, mt = self._prompt_for_round(episode, round_id)
        return await self._gen_replica(episode, model_id, side, round_id, system, user, mt, cfg,
                                       seed_override=seed)

    async def generate_rounds(self, episode: Episode,
                              quickfire_questions: list[str] | None = None) -> Episode:
        pros_cfg = self.config.model(episode.prosecution_model_id)
        def_cfg = self.config.model(episode.defense_model_id)
        if pros_cfg is None or def_cfg is None:
            raise ValueError("Prosecution/defense model not found in config")

        pros_sys = load_prompt("prosecution", pros_cfg["season"]).replace("{thesis}", episode.thesis)
        def_sys = load_prompt("defense", def_cfg["season"]).replace("{thesis}", episode.thesis)
        mt = episode.gen_params.max_tokens
        qmt = episode.gen_params.quickfire_max_tokens

        # --- Round 1 (prosecution || defense) ---
        r1_user = "Deliver your opening statement (Round 1) arguing your assigned side."
        r1p, r1d = await asyncio.gather(
            self._gen_replica(episode, episode.prosecution_model_id, Side.PROSECUTION,
                              "r1_prosecution", pros_sys, r1_user, mt, pros_cfg),
            self._gen_replica(episode, episode.defense_model_id, Side.DEFENSE,
                              "r1_defense", def_sys, r1_user, mt, def_cfg),
        )
        episode.rounds["r1_prosecution"] = [r1p]
        episode.rounds["r1_defense"] = [r1d]

        # --- Quickfire (all questions, both sides, fully parallel) ---
        questions = quickfire_questions or DEFAULT_QUICKFIRE
        tasks = []
        for q in questions:
            qu = f"Quickfire question: {q}\nAnswer in one or two sentences, taking a clear stance."
            tasks.append(self._gen_replica(episode, episode.prosecution_model_id, Side.PROSECUTION,
                                           "qf_pros", pros_sys, qu, qmt, pros_cfg))
            tasks.append(self._gen_replica(episode, episode.defense_model_id, Side.DEFENSE,
                                           "qf_def", def_sys, qu, qmt, def_cfg))
        qf_results = await asyncio.gather(*tasks)

        exchanges = []
        for i, q in enumerate(questions):
            p = qf_results[i * 2]
            d = qf_results[i * 2 + 1]
            p_text = p.used_text or p.text
            d_text = d.used_text or d.text
            # Flag answers that exceeded the ~15 s word budget before trimming.
            over = len(p_text.split()) > WORD_BUDGET or len(d_text.split()) > WORD_BUDGET
            exchanges.append(QuickfireExchange(
                question=q,
                prosecution_answer=trim_to_words(p_text),
                defense_answer=trim_to_words(d_text),
                over_limit=over,
            ))
        threshold = float(self.config.get("quickfire", "variability_threshold", default=0.35))
        select_n = int(self.config.get("quickfire", "questions_selected", default=10))
        method = self.config.get("quickfire", "variability_method", default="lexical")
        judge_fn = self._variability_judge() if method == "llm_judge" else None
        episode.quickfire = score_and_select(
            exchanges, select=select_n, threshold=threshold, method=method, judge_fn=judge_fn)

        # Persist recommended quickfire as r2 replicas (preserve original question order).
        rec = [ex for ex in sorted(episode.quickfire, key=lambda e: questions.index(e.question))
               if ex.recommended]
        for i, ex in enumerate(rec, 1):
            episode.rounds[f"r2_q{i:02d}_prosecution"] = [Replica(
                round_id=f"r2_q{i:02d}_prosecution", side=Side.PROSECUTION,
                model_id=episode.prosecution_model_id, text=ex.prosecution_answer,
                used_text=ex.prosecution_answer)]
            episode.rounds[f"r2_q{i:02d}_defense"] = [Replica(
                round_id=f"r2_q{i:02d}_defense", side=Side.DEFENSE,
                model_id=episode.defense_model_id, text=ex.defense_answer,
                used_text=ex.defense_answer)]

        # --- Round 3 (3 sub-rounds, inject Round 1 opponent text) ---
        r3_tasks = []
        for n in (1, 2, 3):
            r3_tasks.append(self._gen_replica(
                episode, episode.prosecution_model_id, Side.PROSECUTION,
                f"r3_p{n}_prosecution", pros_sys, _r3_user("prosecution", n, r1d.text), mt, pros_cfg))
            r3_tasks.append(self._gen_replica(
                episode, episode.defense_model_id, Side.DEFENSE,
                f"r3_p{n}_defense", def_sys, _r3_user("defense", n, r1p.text), mt, def_cfg))
        r3_results = await asyncio.gather(*r3_tasks)
        for rep in r3_results:
            episode.rounds[rep.round_id] = [rep]

        # --- Round 4 (closing, depends on Round 3) ---
        r3_summary_pros = _strip_delims(" ".join(
            episode.rounds[f"r3_p{n}_prosecution"][0].text for n in (1, 2, 3)))
        r3_summary_def = _strip_delims(" ".join(
            episode.rounds[f"r3_p{n}_defense"][0].text for n in (1, 2, 3)))
        r4_user_pros = (f"[YOUR_REBUTTALS]\n{r3_summary_pros}\n[/YOUR_REBUTTALS]\n\n"
                        "Deliver your closing statement (Round 4). Summarize why your side prevailed.")
        r4_user_def = (f"[YOUR_REBUTTALS]\n{r3_summary_def}\n[/YOUR_REBUTTALS]\n\n"
                       "Deliver your closing statement (Round 4). Summarize why your side prevailed.")
        r4p, r4d = await asyncio.gather(
            self._gen_replica(episode, episode.prosecution_model_id, Side.PROSECUTION,
                              "r4_prosecution", pros_sys, r4_user_pros, mt, pros_cfg),
            self._gen_replica(episode, episode.defense_model_id, Side.DEFENSE,
                              "r4_defense", def_sys, r4_user_def, mt, def_cfg),
        )
        episode.rounds["r4_prosecution"] = [r4p]
        episode.rounds["r4_defense"] = [r4d]

        # Aggregate all flags onto the episode.
        episode.behaviour_flags = [f for reps in episode.rounds.values() for r in reps for f in r.flags]
        return episode
