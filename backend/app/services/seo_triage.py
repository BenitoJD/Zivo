"""Usefulness triage - compatibility re-exports for SEO Gate Engine.

Canonical: ``app.services.seo_gate.evaluate_usefulness``
"""

from __future__ import annotations

from app.services.seo_gate import (  # noqa: F401
    SeoUsefulnessVerdict,
    evaluate_usefulness,
    triage_usefulness,
)

__all__ = [
    "SeoUsefulnessVerdict",
    "evaluate_usefulness",
    "triage_usefulness",
]
