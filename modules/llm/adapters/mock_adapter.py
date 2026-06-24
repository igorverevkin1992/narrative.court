"""MockAdapter -- deterministic offline responses so the pipeline runs without keys.

Produces clean, confident, side-appropriate text (no hedging) so the smoke test
passes offline. Reasoning models also get a synthetic reasoning_content.
"""
from __future__ import annotations

import hashlib
import time

from modules.llm.adapters.base import ModelAdapter
from modules.schemas import GenerationResult


class MockAdapter(ModelAdapter):
    api_format = "mock"

    def generate(self, system, user, temperature, max_tokens, seed=None):
        t0 = time.time()
        sys_upper = system.upper()
        # Detect via the role-declaration line; both prompts mention the other word later.
        side = (
            "defense" if "YOU ARE DEFENSE" in sys_upper
            else "prosecution" if "YOU ARE PROSECUTION" in sys_upper
            else "neutral"
        )
        h = hashlib.sha256(f"{system}{user}{seed}".encode("utf-8")).hexdigest()
        snippet = self._snippet(user, h)

        if max_tokens <= 80:  # quickfire -- diverge stance words so variability clears threshold
            if side == "prosecution":
                content = f"Yes. It was decisive and inevitable. {snippet}"
            else:
                content = f"No. It was avoidable and contingent. {snippet}"
        else:
            stance = "the motion stands" if side == "prosecution" else "the motion fails"
            # A dated, quantified clause stands in for the factual claims a real
            # model would cite -- this is what the objection step (step 6) rules on.
            year = 1980 + int(h[2:4], 16) % 20
            pct = 10 + int(h[4:6], 16) % 80
            content = (
                f"As {side}, I hold that {stance}. {snippet} "
                f"By {year}, the measured shift reached {pct}% — a decisive margin. "
                f"The causal mechanism is direct and the evidence is decisive."
            )

        reasoning = None
        if self.cfg.get("behaviour_profile", {}).get("reasoning"):
            reasoning = (
                f"The strongest line for the {side} is to foreground concrete "
                f"mechanisms and dated facts. [mock-cot {h[:8]}]"
            )

        return GenerationResult(
            content=content,
            reasoning_content=reasoning,
            finish_reason="stop",
            model_id=self.model_id,
            model_version=f"{self.model_name}-mock",
            latency_ms=int((time.time() - t0) * 1000) + 5,
            prompt_hash=self._hash(system, user),
            usage={"mock": True, "max_tokens": max_tokens},
        )

    @staticmethod
    def _snippet(user: str, h: str) -> str:
        words = [w.strip(".,;:?\"'") for w in user.split() if w.isalpha() and len(w) > 5]
        if not words:
            return "The decisive factor is the historical record."
        topic = words[int(h[:2], 16) % len(words)]
        return f"The decisive factor is {topic}."
