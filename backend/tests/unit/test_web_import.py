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


def test_article_from_pasted_text_requires_minimum_length() -> None:
    with pytest.raises(WebImportError):
        article_from_pasted_text("too short")

    article = article_from_pasted_text("A" * 80, title="Notes")
    assert article.title == "Notes"
