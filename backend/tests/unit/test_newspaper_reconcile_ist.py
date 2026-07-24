"""Unit tests for newspaper reconcile gap-fill (DB holes drive work)."""

from datetime import date, datetime, timezone

from app.services.newspaper_naming import resolve_edition_date
from run_newspaper_ingest import plan_gap_fill


def test_db_has_date_skips() -> None:
    day = date(2026, 7, 24)
    seen = [
        (100, "the-hindu", day),
        (101, "the-hindu", day),  # another city — also skip
    ]
    existing = {("the-hindu", day)}
    assert plan_gap_fill(seen, existing) == []


def test_telegram_new_date_cooks() -> None:
    day = date(2026, 7, 24)
    seen = [
        (50, "the-hindu", day),
        (60, "business-standard", day),
    ]
    existing: set[tuple[str, date]] = {("mint", day)}  # unrelated paper already in DB
    assert plan_gap_fill(seen, existing) == [50, 60]


def test_duplicate_cities_same_date_one_edition() -> None:
    day = date(2026, 7, 24)
    # Bangalore 7713 before Delhi 7720 — lowest msg_id wins.
    seen = [
        (7720, "the-hindu", day),  # Delhi, later
        (7713, "the-hindu", day),  # Bangalore, earlier
        (7800, "the-hindu", day),  # another city
    ]
    assert plan_gap_fill(seen, existing=set()) == [7713]


def test_purged_not_in_existing_means_recook() -> None:
    # Caller builds ``existing`` from status <> purged; purged rows omitted → hole.
    day = date(2026, 7, 20)
    seen = [(10, "the-hindu", day)]
    assert plan_gap_fill(seen, existing=set()) == [10]


def test_filename_date_primary_for_edition_day() -> None:
    msg = datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc)  # next IST calendar day
    assert (
        resolve_edition_date(
            filename="TH -Bangalore -24-07-2026.pdf",
            caption="",
            message_date=msg,
        )
        == date(2026, 7, 24)
    )
    assert (
        resolve_edition_date(
            filename="BS Delhi 24‹07‹2026.pdf",
            caption="",
            message_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        == date(2026, 7, 24)
    )


def test_create_edition_imports_reset_via_question_pool_facade() -> None:
    """Deep-import of question_pool_jobs from newspaper caused circular ImportError
    on _cancel_queued_generate_jobs during reconcile ingest. Keep facade path.
    """
    import inspect

    from app.services import newspaper
    from app.services.question_pool import reset_for_new_page_range
    from app.services import question_pool_jobs

    src = inspect.getsource(newspaper.create_edition_from_pdf)
    assert "from app.services.question_pool_jobs import" not in src
    assert "from app.services.question_pool import reset_for_new_page_range" in src
    # Import order that used to fail mid-init: jobs must fully export cancel helpers.
    assert callable(question_pool_jobs._cancel_queued_generate_jobs)
    assert newspaper.create_edition_from_pdf
    assert reset_for_new_page_range is question_pool_jobs.reset_for_new_page_range
