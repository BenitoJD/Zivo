"""Adaptive retrieval gate - compatibility re-exports for Tutor Retrieval Engine.

Canonical: ``app.services.tutor_retrieval``
"""

from __future__ import annotations

from app.services.tutor_retrieval import (  # noqa: F401
    RetrievalGateVerdict,
    decide_retrieval,
    is_conversational_followup,
    needs_retrieval,
)

__all__ = [
    "RetrievalGateVerdict",
    "decide_retrieval",
    "is_conversational_followup",
    "needs_retrieval",
]
