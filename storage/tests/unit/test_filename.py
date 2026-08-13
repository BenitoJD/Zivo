"""Storage filename sanitization — strip path components and unsafe chars."""

from __future__ import annotations

from app.services.minio import safe_storage_filename


def test_strips_directory_components() -> None:
    assert safe_storage_filename("../../etc/passwd") == "passwd"
    assert safe_storage_filename("..\\..\\windows\\system32") == "system32"
    assert safe_storage_filename("subdir/file.pdf") == "file.pdf"


def test_replaces_unsafe_characters() -> None:
    assert safe_storage_filename("hello world.pdf") == "hello_world.pdf"
    assert safe_storage_filename("a$b#c.docx") == "a_b_c.docx"
    assert safe_storage_filename("résumé.pdf") == "r_sum_.pdf"


def test_preserves_safe_characters() -> None:
    assert safe_storage_filename("report-2024_v2.pdf") == "report-2024_v2.pdf"
    assert safe_storage_filename("user_data.csv") == "user_data.csv"


def test_handles_empty_or_pathological() -> None:
    assert safe_storage_filename("") == "upload"
    assert safe_storage_filename("...") == "upload"
    assert safe_storage_filename("/") == "upload"
    assert safe_storage_filename("///") == "upload"


def test_truncates_very_long_names() -> None:
    long = "a" * 500 + ".pdf"
    out = safe_storage_filename(long)
    assert len(out) <= 200
