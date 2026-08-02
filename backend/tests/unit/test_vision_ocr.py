"""Vision OCR fallback — transcribe sparse pages with a vision LLM."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.vision_ocr import transcribe_page_with_vision


def test_transcribe_returns_text_and_caches() -> None:
    db = MagicMock()
    doc = MagicMock()
    doc.id = "00000000-0000-4000-8000-000000000001"
    doc.storage_key = "users/x/file.pdf"
    db.get.return_value = doc
    with (
        patch("app.services.generation_cache.get", return_value=None),
        patch("app.services.vision_ocr._page_image_url", return_value="data:image/png;base64,AAA"),
        patch("app.services.vision_ocr.vision_chat_model_id", return_value=None),
        patch(
            "app.services.vision_ocr.run_coro_in_worker",
            return_value="Photosynthesis converts light into chemical energy.",
        ) as call,
        patch("app.services.generation_cache.put") as put,
    ):
        out = transcribe_page_with_vision(db, doc.id, 3)
    assert "Photosynthesis" in out
    call.assert_called_once()
    put.assert_called_once()


def test_transcribe_blank_returns_empty_and_caches_blank() -> None:
    db = MagicMock()
    doc = MagicMock()
    doc.id = "00000000-0000-4000-8000-000000000001"
    doc.storage_key = "users/x/file.pdf"
    db.get.return_value = doc
    with (
        patch("app.services.generation_cache.get", return_value=None),
        patch("app.services.vision_ocr._page_image_url", return_value="data:image/png;base64,AAA"),
        patch("app.services.vision_ocr.vision_chat_model_id", return_value=None),
        patch("app.services.vision_ocr.run_coro_in_worker", return_value="BLANK"),
        patch("app.services.generation_cache.put") as put,
    ):
        out = transcribe_page_with_vision(db, doc.id, 3)
    assert out == ""
    put.assert_called_once()


def test_transcribe_vision_failure_returns_empty() -> None:
    db = MagicMock()
    doc = MagicMock()
    doc.id = "00000000-0000-4000-8000-000000000001"
    doc.storage_key = "users/x/file.pdf"
    db.get.return_value = doc
    with (
        patch("app.services.generation_cache.get", return_value=None),
        patch("app.services.vision_ocr._page_image_url", return_value="data:image/png;base64,AAA"),
        patch("app.services.vision_ocr.vision_chat_model_id", return_value=None),
        patch(
            "app.services.vision_ocr.run_coro_in_worker",
            side_effect=RuntimeError("vision model down"),
        ),
        patch("app.services.generation_cache.put") as put,
    ):
        out = transcribe_page_with_vision(db, doc.id, 3)
    assert out == ""
    put.assert_not_called()  # don't cache failures


def test_transcribe_cache_hit_skips_llm() -> None:
    db = MagicMock()
    doc = MagicMock()
    doc.id = "00000000-0000-4000-8000-000000000001"
    doc.storage_key = "users/x/file.pdf"
    db.get.return_value = doc
    with (
        patch("app.services.generation_cache.get", return_value="Cached text here."),
        patch("app.services.vision_ocr.run_coro_in_worker") as call,
    ):
        out = transcribe_page_with_vision(db, doc.id, 3)
    assert out == "Cached text here."
    call.assert_not_called()


def test_transcribe_missing_doc_or_image_returns_empty() -> None:
    db = MagicMock()
    db.get.return_value = None
    assert transcribe_page_with_vision(db, "00000000-0000-4000-8000-000000000001", 1) == ""

    db2 = MagicMock()
    doc = MagicMock()
    doc.id = "00000000-0000-4000-8000-000000000001"
    doc.storage_key = "k"
    db2.get.return_value = doc
    with patch("app.services.vision_ocr._page_image_url", return_value=None):
        assert transcribe_page_with_vision(db2, doc.id, 1) == ""
