from __future__ import annotations

import pytest

from app.services.web_import import (
    WebImportError,
    article_from_pasted_text,
    extract_article,
    normalize_public_url,
    paginate_reader_text,
)


def test_normalize_public_url_adds_https() -> None:
    assert normalize_public_url("example.com/post") == "https://example.com/post"


def test_normalize_public_url_blocks_localhost() -> None:
    with pytest.raises(WebImportError):
        normalize_public_url("http://localhost/secret")


def test_normalize_public_url_blocks_decimal_loopback() -> None:
    with pytest.raises(WebImportError) as exc:
        normalize_public_url("http://2130706433/")
    assert exc.value.code == "blocked_url"


def test_normalize_public_url_blocks_metadata_hostname() -> None:
    with pytest.raises(WebImportError) as exc:
        normalize_public_url("http://metadata.google.internal/latest/")
    assert exc.value.code == "blocked_url"


def test_normalize_public_url_blocks_link_local_ip() -> None:
    with pytest.raises(WebImportError) as exc:
        normalize_public_url("http://169.254.169.254/latest/meta-data/")
    assert exc.value.code == "blocked_url"


def test_extract_article_from_simple_html() -> None:
    html = b"""
    <html>
      <head><title>Sample Article</title></head>
      <body>
        <article>
          <h1>Sample Article</h1>
          <p>""" + (b"This is readable study content. " * 20) + b"""</p>
        </article>
      </body>
    </html>
  """
    article = extract_article(html, "text/html", "https://example.com/post")
    assert article.title == "Sample Article"
    assert "readable study content" in article.text
    assert article.source_domain == "example.com"


def test_paginate_reader_text_splits_long_articles() -> None:
    text = "\n\n".join(f"Paragraph {i}. " + ("word " * 120) for i in range(8))
    pages = paginate_reader_text(text, chars_per_page=500)
    assert len(pages) > 1
    assert pages[0]["page"] == 1


def test_paginate_reader_text_breaks_on_part_headings() -> None:
    text = (
        ("Intro paragraph with enough words to stay on page one before the break. " * 20)
        + "\n\nPart 1 · Daily Operations\n\n"
        + ("Body under part one continues with more study text here. " * 15)
        + "\n\nPart 2 · Infrastructure\n\n"
        + ("Body under part two continues with more study text here. " * 15)
    )
    pages = paginate_reader_text(text, chars_per_page=5000)
    assert len(pages) >= 3
    assert any(p["text"].lstrip().startswith("Part 1") for p in pages[1:])
    assert any(p["text"].lstrip().startswith("Part 2") for p in pages[1:])


def test_paginate_reader_text_keeps_toc_parts_together() -> None:
    # TOC lines look like Part headings but sit on a short page — must not each
    # become their own empty study page.
    text = (
        "Avid Pay Complete KT\n\n"
        + "\n\n".join(f"Part {i} · Title {i}" for i in range(1, 6))
        + "\n\n"
        + ("Real section body with plenty of words for studying this topic. " * 40)
    )
    pages = paginate_reader_text(text, chars_per_page=5000)
    toc_only = [p for p in pages if p["text"].startswith("Part ") and len(p["text"]) < 80]
    assert toc_only == []


def test_article_from_pasted_text_requires_minimum_length() -> None:
    with pytest.raises(WebImportError):
        article_from_pasted_text("too short")

    article = article_from_pasted_text("A" * 80, title="Notes")
    assert article.title == "Notes"
