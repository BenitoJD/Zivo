"""Birth-time item difficulty prior — the cold-start fix.

Zivo generates a fresh question per learner that is answered roughly once, so per-item
Elo never accumulates: a cold item would sit at a 0.5 coin-flip and ``difficulty_edge``
would have nothing to select on (it silently falls back to ``sequence``). This module
estimates an item's difficulty *at generation time* from features of the item itself,
on the calibrator's logit scale, so selection works from the very first answer.

It is a PRIOR only. The first real outcome already begins correcting it (``record_outcome``
reads the seeded value as the item's starting difficulty), and it washes out as evidence
accumulates — **outcomes, never this estimate, are the authority** (the 2025 research
line: feature-based prediction is the accepted cold-start, but the LLM never owns truth).

Pure and deterministic — no LLM call, computed off the answer path — and swappable
behind the calibration seam (a richer feature/critic model can replace it without
touching callers). Difficulty is on the same logit scale as ``calibration`` (ability −
difficulty → P(correct)), centered at 0 and clamped to a gentle nudge.
"""

from __future__ import annotations

import re

# Keep the prior modest: real outcomes should dominate within a handful of answers.
PRIOR_CLAMP = 2.0

# Higher-order cognition (apply/analyse/explain-why) tends to be harder than recall.
_HARD_VERBS = re.compile(
    r"\b(appl(?:y|ies|ied)|predict|compare|contrast|analy[sz]e|evaluate|infer|"
    r"distinguish|deriv(?:e|es|ed)|justif(?:y|ies)|synthesi[sz]e|interpret|why|how)\b",
    re.IGNORECASE,
)
_EASY_VERBS = re.compile(
    r"\b(define|identif(?:y|ies)|name|list|recall|state|label|who|when|where|"
    r"what\s+is|which\s+of)\b",
    re.IGNORECASE,
)


def estimate_birth_difficulty(mcq: dict) -> float:
    """Estimate an item's difficulty (logit scale) from its own features.

    Combines, with modest a-priori weights (not tuned to any sample — held-out real
    outcomes validate via the prior gate):

      1. cognitive level — higher-order verbs (analyse/apply/why) harder than recall;
      2. guess rate — more options lower the floor, so harder (centered at 4);
      3. distractor plausibility — homogeneous option lengths read as plausible
         (harder); one option that stands out is a give-away (easier);
      4. stem complexity — longer / multi-clause stems are slightly harder.

    Returns a value in ``[-PRIOR_CLAMP, PRIOR_CLAMP]``, 0 ≈ an average item.
    """
    question = str(mcq.get("question") or mcq.get("stem") or "")
    options = [str(o) for o in (mcq.get("options") or mcq.get("choices") or []) if str(o).strip()]
    angle = str(mcq.get("cognitive_angle") or "")
    haystack = f"{angle}\n{question}"

    score = 0.0

    # 1. Cognitive level — the strongest a-priori signal.
    if _HARD_VERBS.search(haystack):
        score += 0.6
    if _EASY_VERBS.search(haystack):
        score -= 0.6

    # 2. Guess rate by option count (centered at the usual 4).
    if options:
        score += (len(options) - 4) * 0.25

    # 3. Distractor plausibility via option-length spread.
    if len(options) >= 3:
        lengths = [len(o) for o in options]
        mean = sum(lengths) / len(lengths)
        if mean > 0:
            spread = (max(lengths) - min(lengths)) / mean
            # Low spread (uniform, plausible) → harder; high spread (a give-away) → easier.
            score += max(-0.5, min(0.5, 0.6 - spread))

    # 4. Stem complexity by length.
    words = len(question.split())
    score += max(-0.3, min(0.5, (words - 18) / 40.0))

    return max(-PRIOR_CLAMP, min(PRIOR_CLAMP, score))
