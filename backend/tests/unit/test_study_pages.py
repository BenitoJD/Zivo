"""Native-first study page units."""

from __future__ import annotations

from app.services.study_pages import NATIVE_SOFT_SPLIT_CHARS, study_pages_from_native_units
from app.services.web_import import READER_PAGE_CHARS


def test_soft_fallback_when_no_native() -> None:
    blob = "\n\n".join(f"Para {i}. " + ("word " * 100) for i in range(12))
    pages = study_pages_from_native_units([blob], has_native=False)
    assert len(pages) > 1
    assert all(len(p["text"]) <= READER_PAGE_CHARS + 200 for p in pages)


def test_native_units_kept_as_pages() -> None:
    pages = study_pages_from_native_units(
        ["Short page one", "Short page two", "Short page three"],
        has_native=True,
    )
    assert len(pages) == 3
    assert pages[0]["text"] == "Short page one"
    assert pages[2]["page"] == 3


def test_oversized_native_unit_soft_splits() -> None:
    huge = "word " * (NATIVE_SOFT_SPLIT_CHARS // 2)
    pages = study_pages_from_native_units([huge], has_native=True)
    assert len(pages) > 1
