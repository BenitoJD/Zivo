"""Newspaper ad filter + naming heuristics."""

from datetime import datetime, timezone

from app.services.newspaper_ad_filter import classify_page_text, is_editorial
from app.services.newspaper_naming import parse_edition_meta


def test_ad_page_detected() -> None:
    text = (
        "LIMITED PERIOD OFFER buy now call toll free 1800123456. "
        "Subscribe now scan QR. Advertisement for cars. Another advertisement."
    )
    label, _ = classify_page_text(text)
    assert label == "ad"
    assert not is_editorial(text)


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


def test_low_signal_short() -> None:
    label, _ = classify_page_text("hi")
    assert label == "low_signal"


def test_parse_edition_uses_message_date_when_no_date(monkeypatch) -> None:
    class FakeDB:
        pass

    # Avoid DB alias lookups — resolve_alias returns None; upsert is no-op.
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.resolve_alias",
        lambda db, raw: None,
    )
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.upsert_alias",
        lambda *a, **kwargs: None,
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
    )
    assert parsed.paper_slug == "mint"
    assert parsed.edition_date.isoformat() == "2026-07-24"


def test_parse_edition_date_from_filename(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.resolve_alias",
        lambda db, raw: None,
    )
    monkeypatch.setattr(
        "app.services.newspaper_naming.newspaper_repo.upsert_alias",
        lambda *a, **kwargs: None,
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
    )
    assert parsed.edition_date.isoformat() == "2026-07-20"
