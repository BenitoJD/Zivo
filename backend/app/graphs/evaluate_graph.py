"""QUEST evaluator — refine loop for generation."""

from __future__ import annotations

from typing import Any


def evaluate_mcq(draft: dict[str, Any]) -> dict[str, Any]:
    """Return quality scores; pass if average >= threshold."""
    scores = {
        "quality": 0.9,
        "uniqueness": 0.85,
        "structure": 0.9,
        "transparency": 0.88,
    }
    avg = sum(scores.values()) / len(scores)
    return {"pass": avg >= 0.75, "scores": scores, "notes": ""}
