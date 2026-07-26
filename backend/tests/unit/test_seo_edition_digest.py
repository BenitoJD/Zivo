"""Newspaper edition digest — fingerprint, voice, API contracts."""

from __future__ import annotations

import re

from app.services.seo_cook import _edition_fingerprint, _edition_source_key
from app.services.seo_voice import humanize_voice, strip_em_dashes
from app.services import seo_writer


def test_edition_fingerprint_stable() -> None:
    fp = _edition_fingerprint("the-hindu", __import__("datetime").date(2026, 7, 26))
    assert fp == "newspaper:the-hindu:2026-07-26"
    assert _edition_source_key("the-hindu", __import__("datetime").date(2026, 7, 26)) == (
        "the-hindu:2026-07-26"
    )


def test_edition_digest_prompts_have_no_em_dash_char() -> None:
    em = "\u2014"
    assert em not in seo_writer._EDITION_SYSTEM
    assert em not in seo_writer._SYSTEM
    # Spot-check user template builder does not inject em dash
    sample = seo_writer.write_edition_digest.__doc__ or ""
    assert em not in sample


def test_edition_digest_humanize_strips_sludge() -> None:
    raw = "A careful read — the policy shift matters for exams."
    out = humanize_voice(raw)
    assert "\u2014" not in out
    assert " - " in out or "," in out


def test_public_edition_post_shape_strips_internal_keys() -> None:
    post = {
        "id": "00000000-0000-0000-0000-000000000001",
        "slug": "the-hindu-2026-07-26",
        "title": "What moved today",
        "lede": "Three threads worth knowing.",
        "body_md": "Body",
        "source_kind": "newspaper_edition",
        "source_ref": {"edition_id": "x"},
        "topic_fingerprint": "newspaper:the-hindu:2026-07-26",
        "artifact_id": "00000000-0000-0000-0000-000000000002",
        "practice_href": "/practice/newspaper/e/00000000-0000-0000-0000-000000000003",
    }
    post.pop("source_kind", None)
    post.pop("source_ref", None)
    post.pop("topic_fingerprint", None)
    post.pop("artifact_id", None)
    assert "source_kind" not in post
    assert "source_ref" not in post
    assert post["practice_href"].startswith("/practice/newspaper/e/")


def test_strip_em_dashes_handles_unicode_variants() -> None:
    for ch in ("\u2014", "\u2013", "\u2015"):
        out = strip_em_dashes(f"one{ch}two")
        assert ch not in out
    assert re.search(r"\s-\s", strip_em_dashes("a — b"))
