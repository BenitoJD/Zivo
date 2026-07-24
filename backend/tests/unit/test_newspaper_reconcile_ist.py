"""Unit tests for IST day-scoped newspaper reconcile helpers."""

from datetime import date, datetime, timedelta, timezone

from app.services.newspaper_naming import resolve_edition_date
from run_newspaper_ingest import (
    in_edition_lookback,
    lookback_start_date,
    message_ist_date,
    past_lookback,
)


def test_message_ist_date_converts_utc_across_midnight() -> None:
    # 2026-07-23 20:30 UTC == 2026-07-24 02:00 IST
    utc = datetime(2026, 7, 23, 20, 30, tzinfo=timezone.utc)
    assert message_ist_date(utc) == date(2026, 7, 24)


def test_message_ist_date_treats_naive_as_utc() -> None:
    # Telethon often returns naive UTC.
    naive = datetime(2026, 7, 23, 18, 0, 0)
    assert message_ist_date(naive) == date(2026, 7, 23)


def test_lookback_start_covers_today_and_yesterday() -> None:
    # Fixed "now" mid-day IST (05:00 UTC = 10:30 IST on 2026-07-24).
    now = datetime(2026, 7, 24, 5, 0, tzinfo=timezone.utc)
    assert lookback_start_date(now=now, days=2) == date(2026, 7, 23)
    assert lookback_start_date(now=now, days=1) == date(2026, 7, 24)


def test_past_lookback_stops_before_window() -> None:
    now = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)  # 17:30 IST
    in_window = datetime(2026, 7, 23, 1, 0, tzinfo=timezone.utc)  # 06:30 IST Jul-23
    # Jul 22 18:00 UTC = Jul 22 23:30 IST → past 2-day window (23–24).
    older = datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc)
    assert not past_lookback(in_window, now=now, days=2)
    assert past_lookback(older, now=now, days=2)


def test_lookback_includes_early_morning_yesterday_edge() -> None:
    # Just after midnight IST on Jul 24: still scan Jul 23 drops.
    now = datetime(2026, 7, 23, 18, 35, tzinfo=timezone.utc)  # 00:05 IST Jul-24
    yesterday_drop = datetime(2026, 7, 23, 2, 0, tzinfo=timezone.utc)  # 07:30 IST Jul-23
    assert message_ist_date(now) == date(2026, 7, 24)
    assert lookback_start_date(now=now, days=2) == date(2026, 7, 23)
    assert not past_lookback(yesterday_drop, now=now, days=2)
    assert past_lookback(yesterday_drop - timedelta(days=1), now=now, days=2)


def test_filename_dd_mm_yyyy_wins_over_message_date() -> None:
    # Posted next IST day; filename still says 24-07-2026.
    msg = datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc)  # 25 Jul 01:30 IST
    assert message_ist_date(msg) == date(2026, 7, 25)
    assert (
        resolve_edition_date(
            filename="TH -Bangalore -24-07-2026.pdf",
            caption="",
            message_date=msg,
        )
        == date(2026, 7, 24)
    )


def test_filename_fancy_separator_parses() -> None:
    assert (
        resolve_edition_date(
            filename="BS Delhi 24‹07‹2026.pdf",
            caption="",
            message_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        == date(2026, 7, 24)
    )


def test_no_filename_date_falls_back_to_message_ist() -> None:
    msg = datetime(2026, 7, 23, 20, 30, tzinfo=timezone.utc)  # 24 Jul 02:00 IST
    assert (
        resolve_edition_date(
            filename="Mint_Kolkata.pdf",
            caption="",
            message_date=msg,
        )
        == date(2026, 7, 24)
    )


def test_wrong_day_filename_excluded_from_today_scan() -> None:
    # Today IST = 2026-07-24; filename says Jul 22 → outside 2-day lookback.
    now = datetime(2026, 7, 24, 5, 0, tzinfo=timezone.utc)
    edition = resolve_edition_date(
        filename="TH -Delhi -22-07-2026.pdf",
        caption="",
        message_date=now,  # posted "today" on Telegram
    )
    assert edition == date(2026, 7, 22)
    assert not in_edition_lookback(edition, now=now, days=2)
    assert in_edition_lookback(date(2026, 7, 24), now=now, days=2)
    assert in_edition_lookback(date(2026, 7, 23), now=now, days=2)
