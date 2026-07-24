"""Unit tests for IST day-scoped newspaper reconcile helpers."""

from datetime import date, datetime, timedelta, timezone

from run_newspaper_ingest import (
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
