"""Newspaper ad filter + exam relevance + naming heuristics + LLM paper-id learning."""

import re
from datetime import datetime, timezone

from app.services.newspaper_ad_filter import (
    classify_exam_relevance,
    classify_page_text,
    is_editorial,
    newspaper_page_verdict,
    should_cook_newspaper_page,
)
from app.services.newspaper_naming import _norm_key, parse_edition_meta


def test_ad_page_detected() -> None:
    text = (
        "LIMITED PERIOD OFFER buy now call toll free 1800123456. "
        "Subscribe now scan QR. Advertisement for cars. Another advertisement."
    )
    label, _ = classify_page_text(text)
    assert label == "ad"
    assert not is_editorial(text)
    assert newspaper_page_verdict(text)[0] == "ad"
    assert not should_cook_newspaper_page(text)


def test_property_classified_detected() -> None:
    text = (
        "Flat for sale in Andheri. EMI starts at 45k. Sq ft rates from 28000. "
        "Walk-in interview for sales. Call toll free for site visit tomorrow."
    )
    label, _ = classify_page_text(text)
    assert label == "ad"
    assert newspaper_page_verdict(text)[0] == "ad"


def test_editorial_page_ok() -> None:
    text = (
        "The central bank raised rates by 25 basis points yesterday, "
        "citing persistent inflation in food and fuel. Markets reacted "
        "cautiously as bond yields edged higher across the curve. "
        "Economists said the move was widely anticipated after recent data."
    )
    label, _ = classify_page_text(text)
    assert label == "editorial"
    assert is_editorial(text)
    ok, theme, _ = classify_exam_relevance(text)
    assert ok
    assert theme == "economy"
    assert should_cook_newspaper_page(text)


def test_low_signal_short() -> None:
    label, _ = classify_page_text("hi")
    assert label == "low_signal"
    assert newspaper_page_verdict("hi")[0] == "low_signal"


def test_sports_gossip_off_syllabus() -> None:
    text = (
        "Bollywood celebrity gossip dominated the red carpet at fashion week. "
        "The IPL match scorecard showed a high run rate after 16 overs and "
        "four quick wickets. Fans celebrated the box office weekend elsewhere."
    )
    ok, _, _ = classify_exam_relevance(text)
    assert not ok
    assert newspaper_page_verdict(text)[0] in {"off_syllabus", "ad"}
    assert not should_cook_newspaper_page(text)


def test_polity_page_relevant() -> None:
    text = (
        "The Supreme Court examined whether the ordinance issued by the Union "
        "Cabinet complies with the Constitution and fundamental rights doctrine. "
        "Parliament is expected to debate the bill when the Lok Sabha resumes. "
        "Legal scholars said the judgment could reshape federalism debates."
    )
    assert is_editorial(text)
    ok, theme, _ = classify_exam_relevance(text)
    assert ok
    assert theme == "polity"
    assert newspaper_page_verdict(text)[0] == "cook"


def test_lifestyle_without_gs_rejected() -> None:
    text = (
        "A new lifestyle column recommends weekend recipe ideas and cooking tips "
        "for busy professionals who want lighter dinners. Readers shared photos "
        "of their favourite desserts and asked for more fashion week coverage "
        "from the previous season's runway looks in the metro edition."
    )
    ok, _, _ = classify_exam_relevance(text)
    assert not ok
    assert not should_cook_newspaper_page(text)


def test_parse_edition_uses_message_date_when_no_date(monkeypatch) -> None:
    class FakeDB:
        pass

    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.resolve_alias",
        lambda db, raw: None,
    )
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.make_paper_identity",
        lambda title: ("mint", "Mint"),
    )

    msg_date = datetime(2026, 7, 24, 6, 0, tzinfo=timezone.utc)
    parsed = parse_edition_meta(
        FakeDB(),
        filename="Mint_Kolkata.pdf",
        caption="",
        message_date=msg_date,
        skip_llm=True,
    )
    assert parsed.paper_slug == "mint"
    assert parsed.edition_date.isoformat() == "2026-07-24"


def test_parse_edition_date_from_filename(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.resolve_alias",
        lambda db, raw: None,
    )
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.make_paper_identity",
        lambda title: ("economic-times", "Economic Times"),
    )
    msg_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    parsed = parse_edition_meta(
        object(),
        filename="Economic_Times_Delhi_2026-07-20.pdf",
        caption="",
        message_date=msg_date,
        skip_llm=True,
    )
    assert parsed.edition_date.isoformat() == "2026-07-20"


def test_parse_th_via_llm_identity_learns_alias(monkeypatch) -> None:
    """TH -City -DD-MM-YYYY.pdf → LLM says The Hindu; aliases learned (no hardcode)."""
    learned: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.resolve_alias",
        lambda db, raw: None,
    )
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.upsert_alias",
        lambda db, *, alias_key, paper_slug, paper_title: learned.append(
            (alias_key, paper_slug, paper_title)
        ),
    )
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.list_brands",
        lambda db: [{"paper_slug": "the-hindu", "paper_title": "The Hindu", "enabled": True}],
    )

    msg_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    parsed = parse_edition_meta(
        object(),
        filename="TH -Bangalore -24-07-2026.pdf",
        caption="",
        message_date=msg_date,
        llm_identity=("the-hindu", "The Hindu"),
    )
    assert parsed.paper_slug == "the-hindu"
    assert parsed.paper_title == "The Hindu"
    assert parsed.edition_date.isoformat() == "2026-07-24"
    assert "bangalore" in parsed.location_raw.lower()
    assert parsed.source.startswith("llm+")
    assert any(_norm_key(k) == "th" for k, _, _ in learned)
    assert not any(_norm_key(k) == "bangalore" for k, _, _ in learned)


def test_bs_cannot_map_to_the_hindu(monkeypatch) -> None:
    """BS Ahmedabad must not become The Hindu — conflict guard rejects."""
    learned: list[str] = []
    deleted: list[str] = []

    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.resolve_alias",
        lambda db, raw: None,
    )
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.upsert_alias",
        lambda db, *, alias_key, paper_slug, paper_title: learned.append(alias_key),
    )
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.delete_alias",
        lambda db, raw: deleted.append(raw),
    )
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.make_paper_identity",
        lambda title: ("bs", "BS"),
    )

    parsed = parse_edition_meta(
        object(),
        filename="BS  Ahmedabad  24-07-2026.pdf",
        caption="",
        message_date=datetime(2026, 7, 24, tzinfo=timezone.utc),
        llm_identity=("the-hindu", "The Hindu"),
    )
    assert parsed.paper_slug != "the-hindu"
    assert parsed.source.startswith("heuristic+")
    assert learned == []


def test_poisoned_bs_alias_ignored_and_deleted(monkeypatch) -> None:
    """Stale bs→the-hindu alias must not win when filename clearly says BS."""
    deleted: list[str] = []

    def resolve(db, raw):
        key = re.sub(r"[^a-z0-9]+", "", (raw or "").lower())
        if key == "bs":
            return ("the-hindu", "The Hindu")
        return None

    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.resolve_alias",
        resolve,
    )
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.delete_alias",
        lambda db, raw: deleted.append(raw),
    )
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.make_paper_identity",
        lambda title: ("bs", "BS"),
    )
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.upsert_alias",
        lambda db, *, alias_key, paper_slug, paper_title: None,
    )

    parsed = parse_edition_meta(
        object(),
        filename="BS Ahmedabad 24-07-2026.pdf",
        caption="",
        message_date=datetime(2026, 7, 24, tzinfo=timezone.utc),
        skip_llm=True,
    )
    assert parsed.paper_slug != "the-hindu"
    assert any(re.sub(r"[^a-z0-9]+", "", d.lower()) == "bs" for d in deleted)


def test_low_confidence_does_not_learn_alias(monkeypatch) -> None:
    learned: list[str] = []

    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.resolve_alias",
        lambda db, raw: None,
    )
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.upsert_alias",
        lambda db, *, alias_key, paper_slug, paper_title: learned.append(alias_key),
    )

    parsed = parse_edition_meta(
        object(),
        filename="TH -Delhi -24-07-2026.pdf",
        caption="",
        message_date=datetime(2026, 7, 24, tzinfo=timezone.utc),
        llm_identity=("the-hindu", "The Hindu", 0.2, True),  # type: ignore[arg-type]
    )
    assert parsed.paper_slug == "the-hindu"
    # exact catalog hit + confidence below min → still no learn (0.2 < 0.35)
    assert learned == []


def test_heuristic_does_not_poison_alias_table(monkeypatch) -> None:
    upserts: list[str] = []

    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.resolve_alias",
        lambda db, raw: None,
    )
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.upsert_alias",
        lambda db, *, alias_key, paper_slug, paper_title: upserts.append(alias_key),
    )
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.make_paper_identity",
        lambda title: ("th", "TH"),
    )

    parsed = parse_edition_meta(
        object(),
        filename="TH -Delhi -24-07-2026.pdf",
        caption="",
        message_date=datetime(2026, 7, 24, tzinfo=timezone.utc),
        skip_llm=True,
    )
    assert parsed.paper_slug == "th"
    assert upserts == []


def test_soft_match_brand_rejected(monkeypatch) -> None:
    from app.services.newspaper_naming import _match_known_brand

    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.make_paper_identity",
        lambda title: ("hindu-daily", "Hindu Daily"),
    )
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.list_brands",
        lambda db: [{"paper_slug": "the-hindu", "paper_title": "The Hindu", "enabled": True}],
    )
    slug, title, exact = _match_known_brand(object(), "Hindu Daily")
    assert exact is False
    assert slug == "hindu-daily"


def test_glued_city_pdf_hits_brand_alias(monkeypatch) -> None:
    """thdelhi24072026 must resolve via learned 'th' alias — no LLM."""

    def resolve(db, raw):
        if _norm_key(raw) == "th":
            return ("the-hindu", "The Hindu")
        return None

    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.resolve_alias",
        resolve,
    )
    parsed = parse_edition_meta(
        object(),
        filename="thdelhi24072026.pdf",
        caption="",
        message_date=datetime(2026, 7, 24, tzinfo=timezone.utc),
        skip_llm=True,
    )
    assert parsed.paper_slug == "the-hindu"
    assert parsed.source.startswith("alias+")


def test_glued_form_learns_brand_alias_key(monkeypatch) -> None:
    """After LLM IDs The Hindu from glued filename, learn 'th' for next city."""
    learned: list[str] = []

    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.resolve_alias",
        lambda db, raw: None,
    )
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.upsert_alias",
        lambda db, *, alias_key, paper_slug, paper_title: learned.append(_norm_key(alias_key)),
    )
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.list_brands",
        lambda db: [{"paper_slug": "the-hindu", "paper_title": "The Hindu", "enabled": True}],
    )

    parsed = parse_edition_meta(
        object(),
        filename="thmumbai24072026.pdf",
        caption="",
        message_date=datetime(2026, 7, 24, tzinfo=timezone.utc),
        llm_identity=("the-hindu", "The Hindu", 0.9, True),  # type: ignore[arg-type]
    )
    assert parsed.paper_slug == "the-hindu"
    assert "th" in learned


def test_paper_id_fingerprint_shared_across_cities() -> None:
    from app.services.newspaper_naming import _paper_id_fingerprint

    a = _paper_id_fingerprint(filename="TH -Delhi -24-07-2026.pdf", caption="")
    b = _paper_id_fingerprint(filename="TH -Bangalore -25-07-2026.pdf", caption="")
    c = _paper_id_fingerprint(filename="thdelhi24072026.pdf", caption="")
    assert a == b == c == "th"


def test_newspaper_learn_ready_requires_first_mcq() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch
    from uuid import uuid4

    from app.models import Document
    from app.services.newspaper import newspaper_learn_ready

    doc = Document(
        slug="news",
        filename="paper.pdf",
        content_type="application/pdf",
        size_bytes=1,
        storage_key="k",
        status="ready",
        meta={
            "ingest_kind": "newspaper",
            "newspaper": True,
            "selected_range": {"from": 1, "to": 3, "pages": [1, 2, 3]},
        },
    )
    doc.id = uuid4()
    db = SimpleNamespace()

    with patch("app.services.question_pool.get_page_coverage", return_value={}):
        assert newspaper_learn_ready(db, doc) is False

    with (
        patch(
            "app.services.question_pool.get_page_coverage",
            return_value={"question_budget": 5, "non_content": False},
        ),
        patch("app.services.question_pool_jobs.count_assertions_on_page", return_value=0),
    ):
        assert newspaper_learn_ready(db, doc) is False

    with (
        patch(
            "app.services.question_pool.get_page_coverage",
            return_value={"question_budget": 5, "non_content": False},
        ),
        patch("app.services.question_pool_jobs.count_assertions_on_page", return_value=2),
    ):
        assert newspaper_learn_ready(db, doc) is True


def test_newspaper_learn_queue_uses_edition_wide_pool() -> None:
    from unittest.mock import MagicMock, patch
    from uuid import uuid4

    from app.models import Document
    from app.services.question_pool import build_learn_queue_state

    doc = Document(
        slug="news",
        filename="paper.pdf",
        content_type="application/pdf",
        size_bytes=1,
        storage_key="k",
        status="ready",
        meta={
            "ingest_kind": "newspaper",
            "newspaper": True,
            "selected_range": {"from": 1, "to": 3, "pages": [1, 2, 3]},
            "question_pool_initialized": True,
        },
    )
    doc.id = uuid4()
    progress = {
        "current_page": 1,
        "answered_ids": ["q1"],
        "generation_pending": False,
        "page_coverage": {"1": {"question_budget": 2}},
    }
    edition_ids = ["q1", "q2", "q3", "q4"]
    db = MagicMock()

    with (
        patch("app.services.newspaper.is_newspaper_document", return_value=True),
        patch("app.services.question_pool.edition_assertion_ids", return_value=edition_ids),
        patch("app.services.question_pool.select_next_assertion", return_value="q2"),
        patch("app.services.question_pool.assertion_page_number", return_value=2),
        patch("app.services.question_pool.get_page_coverage", return_value={"question_budget": 2}),
        patch("app.services.question_pool.is_coverage_complete", return_value=False),
        patch("app.services.question_pool.page_budgets_for_document", return_value=[]),
        patch("app.services.rag_window.chat_rag_window", return_value=[1, 2, 3]),
        patch("app.services.rag_window.is_rag_window_ready", return_value=True),
    ):
        state = build_learn_queue_state(db, doc.id, doc, progress)

    assert state["edition_pool"] is True
    assert state["questions_generated"] == 4
    assert state["questions_answered"] == 1
    assert state["question_number"] == 2
    assert state["current_page"] == 2
    assert state["page_complete"] is False
    assert state["current_assertion_id"] == "q2"


def test_newspaper_get_progress_merges_learner_overlay() -> None:
    from unittest.mock import MagicMock, patch
    from uuid import uuid4

    from sqlalchemy.orm import Session

    from app.models import Document
    from app.services.question_pool import get_progress

    doc = Document(
        slug="news",
        filename="paper.pdf",
        content_type="application/pdf",
        size_bytes=1,
        storage_key="k",
        status="ready",
        meta={"ingest_kind": "newspaper", "newspaper": True},
    )
    doc.id = uuid4()
    db = MagicMock()

    with (
        patch.object(Session, "object_session", return_value=db),
        patch("app.services.newspaper.is_newspaper_document", return_value=True),
        patch(
            "app.services.question_pool._load_shared_progress",
            return_value={"current_page": 1, "answered_ids": ["shared-should-not-use"]},
        ),
        patch(
            "app.services.question_pool.load_learner_progress",
            return_value={"answered_ids": ["learner-only"]},
        ),
    ):
        progress = get_progress(doc, learner_key="user:abc")

    assert progress["answered_ids"] == ["learner-only"]