"""Newspaper ad filter + exam relevance + naming heuristics + LLM paper-id learning."""

from datetime import datetime, timezone

from app.services.newspaper_ad_filter import (
    classify_exam_relevance,
    classify_page_text,
    is_editorial,
    newspaper_page_verdict,
    should_cook_newspaper_page,
)
from app.services.newspaper_naming import parse_edition_meta


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
    assert any(k.upper() == "TH" or k == "TH" for k, _, _ in learned)


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
