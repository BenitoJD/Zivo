"""Document parsing — PDF, DOCX, plain text, imported articles."""

import hashlib
import io
import json

import fitz
from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.services.web_import import paginate_reader_text

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


def count_document_pages(content_type: str, data: bytes) -> int:
    """Page count for upload meta — PDF physical pages; Word/text via parse."""
    ct = (content_type or "").lower()
    if "pdf" in ct or data[:4] == b"%PDF":
        return count_pdf_pages(data)
    return max(1, len(parse_document(content_type, data)))


def refresh_document_page_count(db, doc) -> int:
    """Ensure ``meta.page_count`` matches the file (heals pre-soft-paginate DOCX).

    Cheap for PDF (fitz page count). For Word/text/paste, re-parses once when the
    stored count is missing or still ``1`` on a non-trivial file — those were the
    docs that got stuck as a single mega-page before soft pagination shipped.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.services.storage import fetch_object

    meta = dict(doc.meta or {})
    stored = meta.get("page_count")
    try:
        stored_i = int(stored) if stored is not None else None
    except (TypeError, ValueError):
        stored_i = None

    ct = (doc.content_type or "").lower()
    if ct.startswith("image/"):
        return stored_i or 1

    # Already a real multi-page count — trust it.
    if stored_i is not None and stored_i > 1:
        return stored_i

    # Tiny files are genuinely one page; skip a MinIO round-trip.
    if int(getattr(doc, "size_bytes", 0) or 0) < 2500 and stored_i == 1:
        return 1

    try:
        data = fetch_object(doc.storage_key)
        counted = count_document_pages(doc.content_type, data)
    except Exception:
        return stored_i or 1

    if counted == stored_i:
        return counted or 1

    meta["page_count"] = counted
    # Old DOCX path stored the whole file as page 1 and auto-selected [1].
    # Clear that so the learner picks a real study range on the healed pages.
    selected = meta.get("selected_range") if isinstance(meta.get("selected_range"), dict) else None
    if counted > 1 and selected:
        pages = selected.get("pages")
        only_page_one = (
            (isinstance(pages, list) and pages == [1])
            or (
                int(selected.get("from") or 0) == 1
                and int(selected.get("to") or 0) == 1
                and not pages
            )
        )
        if only_page_one:
            meta.pop("selected_range", None)
            if getattr(doc, "status", None) in {"ready", "indexing"}:
                doc.status = "pending"
                doc.index_progress = 0
    doc.meta = meta
    flag_modified(doc, "meta")
    db.commit()
    return counted


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


def _iter_docx_blocks(doc: DocxDocument):
    """Yield paragraphs and tables in document body order."""
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield "p", Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield "t", Table(child, doc)


def _table_text(table: Table) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _paragraph_page_break_before(paragraph: Paragraph) -> bool:
    p_pr = paragraph._element.find(qn("w:pPr"))
    return p_pr is not None and p_pr.find(qn("w:pageBreakBefore")) is not None


def _split_paragraph_by_page_breaks(paragraph: Paragraph) -> list[str]:
    """Split one paragraph on hard + Word-rendered page breaks; always ≥1 segment."""
    if not paragraph.runs:
        return [paragraph.text or ""]

    segments: list[str] = [""]
    for run in paragraph.runs:
        for child in run._element:
            # Hard break (Insert → Page Break) and Word's last layout break.
            if child.tag == qn("w:lastRenderedPageBreak") or (
                child.tag == qn("w:br") and child.get(qn("w:type")) == "page"
            ):
                segments.append("")
            elif child.tag == qn("w:t"):
                segments[-1] += child.text or ""
            elif child.tag == qn("w:tab"):
                segments[-1] += "\t"
    return segments


def _paginate_hard_segments(segments: list[str]) -> list[dict]:
    """Soft-paginate each hard page segment (same char budget as paste/URL)."""
    out: list[dict] = []
    page_num = 1
    for segment in segments:
        text = segment.strip()
        if not text:
            continue
        for item in paginate_reader_text(text):
            out.append({"page": page_num, "text": item["text"]})
            page_num += 1
    return out or [{"page": 1, "text": ""}]


def _parse_docx(data: bytes) -> list[dict]:
    """Split Word docs on hard page breaks; otherwise soft-paginate like paste."""
    doc = DocxDocument(io.BytesIO(data))
    hard_segments: list[str] = []
    current: list[str] = []

    def flush() -> None:
        hard_segments.append("\n\n".join(part for part in current if part.strip()).strip())
        current.clear()

    def add_text(text: str) -> None:
        stripped = text.strip()
        if stripped:
            current.append(stripped)

    for kind, block in _iter_docx_blocks(doc):
        if kind == "t":
            add_text(_table_text(block))
            continue
        paragraph: Paragraph = block
        if _paragraph_page_break_before(paragraph) and current:
            flush()
        parts = _split_paragraph_by_page_breaks(paragraph)
        for i, part in enumerate(parts):
            if i > 0:
                flush()
            add_text(part)

    flush()
    while len(hard_segments) > 1 and not hard_segments[-1]:
        hard_segments.pop()
    return _paginate_hard_segments(hard_segments)


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
    return paginate_reader_text(text.strip())
