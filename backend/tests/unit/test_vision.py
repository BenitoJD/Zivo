"""Unit tests for multimodal vision helpers."""

from __future__ import annotations

import uuid
from unittest.mock import patch

from app.models import Document
from app.services.vision import build_user_message, is_image_document


def _doc(**kwargs) -> Document:
    return Document(
        id=uuid.uuid4(),
        slug="photo",
        filename="photo.png",
        content_type=kwargs.pop("content_type", "image/png"),
        size_bytes=4,
        storage_key="demo/photo.png",
        **kwargs,
    )


def test_is_image_document() -> None:
    assert is_image_document(_doc())
    assert not is_image_document(_doc(content_type="application/pdf"))


def test_build_user_message_plain_text() -> None:
    doc = _doc(content_type="text/plain")
    assert build_user_message("hello", doc, include_image=True) == "hello"


@patch("app.services.vision.fetch_object", return_value=b"\x89PNG")
def test_build_user_message_with_image(_mock_fetch: object) -> None:
    doc = _doc()
    content = build_user_message("What is this?", doc, include_image=True)
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "What is this?"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
