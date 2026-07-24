"""Document parsing — PDF, DOCX, plain text, imported articles."""

import hashlib
import io
import json

import fitz
from docx import Document as DocxDocument

_SPARSE_PAGE_CHARS = 40


def document_bytes_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
            if len(text) < _SPARSE_PAGE_CHARS:
                blocks = page.get_text("blocks")
                block_text = "\n".join(
                    str(block[4]).strip()
                    for block in blocks
                    if isinstance(block, (list, tuple)) and len(block) > 4 and str(block[4]).strip()
                ).strip()
                if len(block_text) > len(text):
                    text = block_text
            entry: dict = {"page": i, "text": text}
            if len(text) < _SPARSE_PAGE_CHARS:
                entry["sparse_text"] = True
            pages.append(entry)
    return pages or [{"page": 1, "text": "", "sparse_text": True}]


def parse_pdf_page(data: bytes, page_number: int) -> dict:
    """Extract text for a single 1-indexed PDF page."""
    page_number = max(1, int(page_number))
    with fitz.open(stream=data, filetype="pdf") as doc:
        if page_number > doc.page_count:
            return {"page": page_number, "text": ""}
        text = doc[page_number - 1].get_text("text").strip()
        return {"page": page_number, "text": text}


def parse_document_page(
    content_type: str,
    data: bytes,
    page_number: int,
    *,
    cached_pages: list[dict] | None = None,
) -> dict:
    """Return {page, text} for one page (PDF) or the whole doc for single-page formats."""
    page_number = int(page_number)
    if cached_pages:
        for item in cached_pages:
            if int(item.get("page", 0)) == page_number:
                return {"page": page_number, "text": str(item.get("text") or "").strip()}
        return {"page": page_number, "text": ""}
    ct = (content_type or "").lower()
    if "pdf" in ct or data[:4] == b"%PDF":
        return parse_pdf_page(data, page_number)
    pages = parse_document(content_type, data)
    if len(pages) == 1:
        return pages[0]
    for item in pages:
        if int(item.get("page", 0)) == page_number:
            return item
    return {"page": page_number, "text": ""}


def count_pdf_pages(data: bytes) -> int:
    with fitz.open(stream=data, filetype="pdf") as doc:
        return max(1, doc.page_count)


def render_pdf_page_png(
    data: bytes,
    page_number: int,
    *,
    max_edge: int = 1600,
    max_bytes: int = 4 * 1024 * 1024,
) -> bytes | None:
    """Render one 1-indexed PDF page to a size-capped PNG for vision models."""
    page_number = max(1, int(page_number))
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            if page_number > doc.page_count:
                return None
            page = doc[page_number - 1]
            rect = page.rect
            long_edge = max(float(rect.width), float(rect.height), 1.0)
            scale = min(2.0, max_edge / long_edge)
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            png = pix.tobytes("png")
            # Shrink if still over the vision payload cap.
            while len(png) > max_bytes and scale > 0.35:
                scale *= 0.75
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                png = pix.tobytes("png")
            if len(png) > max_bytes:
                return None
            return png
    except Exception:
        return None


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
