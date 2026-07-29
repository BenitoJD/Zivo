"""LLM newspaper exam-relevance judge — the replacement for the keyword allowlist.

These run against a real DB + LLM and skip otherwise (mirrors
test_rate_limit_db.py / test_llm_registry_concurrency.py). They assert the
contract the old hardcoded tests asserted, but through the LLM engine:
a paraphrased polity page (no literal "supreme court" keyword phrasing required
to appear verbatim) is recognized as relevant, while pure entertainment is not.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import SessionLocal


def _db_reachable() -> bool:
    try:
        db = SessionLocal()
        db.execute(select(1))
        db.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="dev DB not reachable")


def test_paraphrased_polity_is_relevant() -> None:
    """A polity page phrased WITHOUT the literal allowlist keywords is relevant.

    This is the regression the keyword gate failed: "protests / FIRs / Centre"
    never matched "supreme court / fundamental rights". The LLM judges by meaning.
    """
    from app.services.newspaper_relevance import judge_newspaper_relevance

    # Paraphrased governance content — no textbook keyword appears verbatim.
    text = (
        "The bench observed that police action against the protesters must satisfy "
        "the test of proportionality, and that the Centre's assurance against "
        "punitive measures would be examined. Several members demanded that the "
        "first information reports filed in the States be withdrawn, arguing that "
        "dissent could not be criminalised merely because the demonstration turned "
        "large. The Opposition walked out after the chair declined a discussion."
    )
    db = SessionLocal()
    try:
        verdict = judge_newspaper_relevance(db, text)
        assert verdict["relevant"] is True, f"expected relevant, got {verdict}"
    finally:
        db.close()


def test_pure_entertainment_is_not_relevant() -> None:
    """Sports / celebrity gossip is not exam-relevant."""
    from app.services.newspaper_relevance import judge_newspaper_relevance

    text = (
        "Bollywood celebrity gossip dominated the red carpet at fashion week. "
        "The IPL match scorecard showed a high run rate after 16 overs and "
        "four quick wickets. Fans celebrated the box office weekend elsewhere "
        "and a reality show contestant leaked the next episode's twist."
    )
    db = SessionLocal()
    try:
        verdict = judge_newspaper_relevance(db, text)
        assert verdict["relevant"] is False, f"expected not relevant, got {verdict}"
    finally:
        db.close()


def test_verdict_is_cached() -> None:
    """A repeat call for the same text is served from cache (no re-bill).

    The cached dict carries the same verdict; this guards the content-hash key.
    """
    from app.services.newspaper_relevance import judge_newspaper_relevance

    text = (
        "Parliament passed the amendment after the standing committee submitted "
        "its report, expanding the scope of the welfare scheme and increasing "
        "the fiscal allocation for the social sector programme this fiscal year."
    )
    db = SessionLocal()
    try:
        first = judge_newspaper_relevance(db, text)
        second = judge_newspaper_relevance(db, text)
        assert first == second, "cached verdict should equal the first call"
    finally:
        db.close()
