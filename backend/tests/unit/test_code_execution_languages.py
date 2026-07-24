"""Language allowlist — no silent remap to Python 71."""

from __future__ import annotations

import pytest

from app.services.code_execution import (
    DEFAULT_LANGUAGE_ID,
    LANGUAGES,
    ensure_language,
)


def test_top10_allowlist_includes_default_and_new_langs() -> None:
    assert DEFAULT_LANGUAGE_ID == 71
    assert 71 in LANGUAGES
    assert 73 in LANGUAGES  # Rust
    assert 74 in LANGUAGES  # TypeScript
    assert 51 in LANGUAGES  # C#
    assert 78 in LANGUAGES  # Kotlin
    assert len(LANGUAGES) == 10


def test_ensure_language_accepts_allowlisted() -> None:
    assert ensure_language(71) == 71
    assert ensure_language(54) == 54


def test_ensure_language_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unsupported language_id"):
        ensure_language(9999)
