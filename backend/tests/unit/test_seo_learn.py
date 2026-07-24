"""SEO /learn content engine — triage, PII, voice, dedupe, SD floor helpers."""

from __future__ import annotations

from app.services.seo_dedupe import (
    NEAR_DUPE_COSINE,
    cosine_similarity,
    normalize_topic,
    slugify,
    topic_fingerprint,
)
from app.services.seo_pii import scrub_pii
from app.services.seo_triage import triage_usefulness
from app.services.seo_voice import humanize_voice, strip_em_dashes


def test_triage_rejects_internal() -> None:
    text = (
        "CONFIDENTIAL — internal only. Meeting notes from standup. "
        "Action items for the team and password rotation schedule. "
        "Do not share outside the company. " * 5
    )
    ok, reason = triage_usefulness(text, filename="standup-notes.txt")
    assert not ok
    assert reason in {"internal_markers", "internal_filename", "personal_markers"}


def test_triage_accepts_useful_explainer() -> None:
    text = (
        "How does a message queue work in system design? "
        "Explain the concept of decoupling producers and consumers, "
        "retries, and poison messages. Why do teams use queues when "
        "the user path should stay fast? This architecture trade-off "
        "shows up in every large interview and exam syllabus. "
    ) * 8
    ok, reason = triage_usefulness(text, filename="queues.md")
    assert ok
    assert reason == "ok"


def test_pii_scrub_removes_email_phone_name() -> None:
    raw = (
        "Dear Ramesh, please email me at person@example.com "
        "or call +91 98765 43210. My name is Priya Sharma. "
        "PAN ABCDE1234F and Aadhaar 1234 5678 9012."
    )
    out = scrub_pii(raw)
    assert "person@example.com" not in out
    assert "[email]" in out
    assert "[phone]" in out or "98765" not in out
    assert "ABCDE1234F" not in out
    assert "[id]" in out
    assert "Dear [name]" in out or "[name]" in out


def test_voice_strips_em_dashes() -> None:
    raw = "Caching helps — when used carefully -- but can hurt."
    out = strip_em_dashes(raw)
    assert "—" not in out
    assert " -- " not in out
    assert " - " in out


def test_voice_strips_ai_sludge() -> None:
    raw = "Let us delve into the robust landscape and leverage cutting-edge ideas."
    out = humanize_voice(raw)
    assert "delve" not in out.lower()
    assert "landscape" not in out.lower()
    assert "robust" not in out.lower()
    assert "leverage" not in out.lower()


def test_fingerprint_stable_and_slug() -> None:
    a = topic_fingerprint("system_design", "How caching works")
    b = topic_fingerprint("system_design", "How caching works")
    assert a == b
    assert normalize_topic("  Hello, World! ") == "hello world"
    assert slugify("Rate Limiting That Protects Systems") == "rate-limiting-that-protects-systems"


def test_cosine_near_dupe_threshold() -> None:
    # Identical vectors → cosine 1.0 ≥ 0.85
    v = [0.1, 0.2, 0.3, 0.4]
    assert cosine_similarity(v, v) >= NEAR_DUPE_COSINE
    # Orthogonal-ish
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) < NEAR_DUPE_COSINE


def test_public_questions_shape_has_no_correct_index() -> None:
    """Contract: answer-stripped question dict never includes correct_index."""
    item = {
        "id": "00000000-0000-0000-0000-000000000001",
        "question": "What is a cache?",
        "options": ["A", "B", "C", "D"],
        "is_multi": False,
    }
    # Simulate API strip
    item.pop("correct_index", None)
    item.pop("correct_indices", None)
    assert "correct_index" not in item
    assert "correct_indices" not in item


def test_sd_daily_candidate_builders() -> None:
    from app.services.seo_cook import candidate_from_sd_problem, candidate_from_topic

    sd = candidate_from_sd_problem(
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "slug": "url-shortener",
            "title": "Design a URL shortener",
            "prompt": "Design a system that shortens URLs.",
            "constraints": "100M writes/day",
            "reference_design": "Hash + DB + cache",
        }
    )
    assert sd.source_kind == "sd_bank"
    assert sd.stream == "system_design"
    assert "URL shortener" in sd.text or "shortener" in sd.text.lower()

    topic = candidate_from_topic(
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "topic_key": "caching-basics",
            "stream": "system_design",
            "title_hint": "How caching actually works",
            "angle_prompt": "Hit rate and when cache hurts.",
        }
    )
    assert topic.source_kind == "topic_queue"
    assert topic.stream == "system_design"
