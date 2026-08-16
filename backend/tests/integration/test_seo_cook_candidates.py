"""Newspaper cook candidates collapse to distinct pages (no chunk fan-out).

A page is split across many token-window chunk rows in ``qb.document_chunks``.
``list_newspaper_cook_candidates`` must return ONE row per (document, page) with
the page's full text concatenated, not raw chunk rows. Before the fix the query
returned overlapping fragments: 8 candidate slots held only ~2 distinct pages
each a ~900-char slice, so the worthiness gate saw incomplete text (missing the
full page's keywords) and the batch burned slots on duplicate source_keys.

Auto-skips when the dev DB is not reachable (mirrors test_rate_limit_db.py).
"""

from __future__ import annotations

from collections import Counter

import pytest
from sqlalchemy import select, text

from app.db import SessionLocal
from app.engine_runtime import pick
from app.repositories import seo


def _db_reachable() -> bool:
    try:
        db = SessionLocal()
        db.execute(select(1))
        db.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="dev DB not reachable")


def test_candidates_are_distinct_pages_not_chunk_fragments() -> None:
    """Each returned candidate is a distinct (document, page) — no duplicates.

    The dominant pre-fix bug was fan-out: the same source_key appeared multiple
    times in a batch. Asserting zero duplicate source_keys is the direct guard.
    """
    db = SessionLocal()
    try:
        rows = seo.list_newspaper_cook_candidates(db, limit=8)
        pick(not rows, lambda: pytest.skip("no newspaper candidates in dev DB"), lambda: None)
        keys = [f"{r['document_id']}:{r['page_start']}" for r in rows]
        dupes = dict(filter(lambda kn: kn[1] > 1, Counter(keys).items()))
        assert not dupes, f"candidate source_keys should be unique, got duplicates: {dupes}"
        assert len(rows) == len(set(keys)), "rows must map 1:1 to distinct pages"
    finally:
        db.close()


def test_candidate_text_is_full_page_not_fragment() -> None:
    """The candidate text is the aggregated page, not one ~900-char chunk window.

    A page worth cooking spans several thousand characters; a single chunk
    window is capped near 900. Re-aggregating the raw chunk rows for the same
    (document, page) must match the candidate's text length.
    """
    db = SessionLocal()
    try:
        rows = seo.list_newspaper_cook_candidates(db, limit=8)
        pick(not rows, lambda: pytest.skip("no newspaper candidates in dev DB"), lambda: None)
        row = rows[0]
        agg_len = db.execute(
            text(
                "SELECT length(string_agg(c.text, ' ' ORDER BY c.id)) "
                "FROM qb.document_chunks c "
                "WHERE c.document_id = :d AND c.page_start = :p"
            ),
            {"d": row["document_id"], "p": row["page_start"]},
        ).scalar()
        assert agg_len is not None, "candidate page has no chunk rows"
        # candidate text length must equal the full aggregated page (allow trailing
        # whitespace trim), proving we returned the whole page, not one fragment.
        assert len(row["text"]) == len(row["text"].rstrip())
        assert len(row["text"]) >= agg_len - 1  # trimmed trailing space at most
        # And it must be substantially larger than a single chunk window (~900),
        # since that was the symptom: a fragment masquerading as a page.
        single_chunk = db.execute(
            text(
                "SELECT length(text) FROM qb.document_chunks "
                "WHERE document_id = :d AND page_start = :p ORDER BY id LIMIT 1"
            ),
            {"d": row["document_id"], "p": row["page_start"]},
        ).scalar()
        def _check_chunk() -> None:
            assert len(row["text"]) > single_chunk, (
                "returned a single chunk fragment, not the aggregated page"
            )

        pick(bool(single_chunk) and agg_len > single_chunk, _check_chunk, lambda: None)
    finally:
        db.close()
