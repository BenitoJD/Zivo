"""SEO Gate Engine - usefulness + dedupe verdict shape."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.seo_gate import (
    NEAR_DUPE_COSINE,
    SEO_GATE_VERSION,
    evaluate_dedupe,
    evaluate_usefulness,
)


def test_usefulness_rejects_internal() -> None:
    text = (
        "CONFIDENTIAL - internal only. Meeting notes from standup. "
        "Action items for the team and password rotation schedule. "
        "Do not share outside the company. " * 5
    )
    v = evaluate_usefulness(text, filename="standup-notes.txt")
    assert not v.useful
    assert v.reason in {"internal_markers", "internal_filename", "personal_markers"}
    assert v.policy_version == SEO_GATE_VERSION


def test_usefulness_accepts_explainer() -> None:
    text = (
        "How does a message queue work in system design? "
        "Explain the concept of decoupling producers and consumers, "
        "retries, and poison messages. Why do teams use queues when "
        "the user path should stay fast? This architecture trade-off "
        "shows up in every large interview and exam syllabus. "
    ) * 8
    v = evaluate_usefulness(text, filename="queues.md")
    assert v.useful
    assert v.reason == "ok"


def test_dedupe_ok_when_plumbing_clears() -> None:
    db = MagicMock()
    with patch(
        "app.services.seo_dedupe.check_dedupe",
        return_value=(True, "ok"),
    ) as check:
        v = evaluate_dedupe(
            db,
            fingerprint="queues:abc123",
            title="How queues work",
            lede="A short lede about message queues.",
        )
    check.assert_called_once()
    assert v.ok is True
    assert v.reason == "ok"
    assert v.policy_version == SEO_GATE_VERSION


def test_dedupe_rejects_near_dupe() -> None:
    db = MagicMock()
    with patch(
        "app.services.seo_dedupe.check_dedupe",
        return_value=(False, f"embedding_near_dupe:{NEAR_DUPE_COSINE:.3f}"),
    ):
        v = evaluate_dedupe(
            db,
            fingerprint="queues:abc123",
            title="How queues work",
            lede="A short lede about message queues.",
        )
    assert v.ok is False
    assert "embedding_near_dupe" in v.reason
    assert v.policy_version == SEO_GATE_VERSION


def test_seo_near_dupe_threshold_owned_by_gate() -> None:
    assert NEAR_DUPE_COSINE == 0.85


def test_publish_cap() -> None:
    from app.services.seo_gate import evaluate_publish_cap

    blocked = evaluate_publish_cap(published_today=20, soft_max_per_day=20)
    assert blocked.allow is False and blocked.reason == "soft_max"
    ok = evaluate_publish_cap(published_today=3, soft_max_per_day=20)
    assert ok.allow is True and ok.remaining == 17


class _FixedRng:
    def __init__(self, roll: float, choice: str) -> None:
        self._roll = roll
        self._choice = choice

    def random(self) -> float:
        return self._roll

    def choice(self, seq: list[str]) -> str:
        return self._choice


def test_plan_article_presentation_mix() -> None:
    from app.services.seo_gate import plan_article_presentation

    explainer = plan_article_presentation("general", rng=_FixedRng(0.10, "practice"))
    assert explainer.format == "explainer"
    assert explainer.cta_kind == "practice"
    faq = plan_article_presentation("general", rng=_FixedRng(0.80, "signup"))
    assert faq.format == "faq"
    listing = plan_article_presentation("general", rng=_FixedRng(0.90, "signup"))
    assert listing.format == "list"
    sd = plan_article_presentation("system_design", rng=_FixedRng(0.10, "practice"))
    assert sd.cta_kind == "system_design"
    forced = plan_article_presentation(
        "general", format_override="faq", rng=_FixedRng(0.10, "practice")
    )
    assert forced.format == "faq"
