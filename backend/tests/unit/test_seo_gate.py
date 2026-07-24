"""SEO Gate Engine - usefulness + dedupe verdict shape."""

from __future__ import annotations

from app.services.seo_gate import SEO_GATE_VERSION, evaluate_usefulness


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
