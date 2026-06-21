"""Document parsing — PDF, DOCX, plain text, imported articles."""

import io
import json

import fitz
from docx import Document as DocxDocument


def parse_document(content_type: str, data: bytes) -> list[dict]:
    """Return list of {page: int, text: str} (1-indexed pages)."""
    ct = (content_type or "").lower()
    if "pdf" in ct or data[:4] == b"%PDF":
        return _parse_pdf(data)
    if "wordprocessingml" in ct or "msword" in ct:
        return _parse_docx(data)
    if ct.startswith("text/") or ct in ("application/json",):
        return _parse_text(data)
    # images / unknown — single page placeholder for vision path later
    return [{"page": 1, "text": ""}]


def _parse_pdf(data: bytes) -> list[dict]:
    pages: list[dict] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            pages.append({"page": i, "text": text})
    return pages or [{"page": 1, "text": ""}]


def _parse_docx(data: bytes) -> list[dict]:
    doc = DocxDocument(io.BytesIO(data))
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    text = "\n\n".join(paragraphs)
    return [{"page": 1, "text": text}] if text else [{"page": 1, "text": ""}]


def _parse_text(data: bytes) -> list[dict]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(text)
            pages = payload.get("pages")
            if isinstance(pages, list) and pages:
                parsed: list[dict] = []
                for item in pages:
                    if not isinstance(item, dict):
                        continue
                    page_num = int(item.get("page", len(parsed) + 1))
                    page_text = str(item.get("text") or "").strip()
                    parsed.append({"page": page_num, "text": page_text})
                if parsed:
                    return parsed
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return [{"page": 1, "text": text.strip()}]
