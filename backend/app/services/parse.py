"""Document parsing — PDF, DOCX, PPTX, plain text, imported articles.

Study pages follow a native-first policy (see ``app.services.study_pages``):
use the format's own page/slide/break units when present; soft-paginate only
when the format has no native paging.
"""

import hashlib
import io
import json
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import fitz
from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.services.study_pages import study_pages_from_native_units
from app.services.web_import import paginate_reader_text

_SPARSE_PAGE_CHARS = 40
_PPTX_CT = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_A_NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
_P_NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main"}
_R_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
_PR_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def document_bytes_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_document(content_type: str, data: bytes) -> list[dict]:
    """Return list of {page: int, text: str} (1-indexed study pages)."""
    ct = (content_type or "").lower()
    if "pdf" in ct or data[:4] == b"%PDF":
        return _parse_pdf(data)
    if "wordprocessingml" in ct or "msword" in ct:
        return _parse_docx(data)
    if "presentationml" in ct or ct == _PPTX_CT:
        return _parse_pptx(data)
    if ct.startswith("text/") or ct in ("application/json",):
        return _parse_text(data)
    # images / unknown — single page placeholder for vision path later
    return [{"page": 1, "text": ""}]


def _parse_pdf(data: bytes) -> list[dict]:
    """PDF physical pages are native units — never soft-merge them."""
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
    """Return {page, text} for one study page."""
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
    """Page count for upload meta — native units when present, else parse length."""
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


def _parse_docx(data: bytes) -> list[dict]:
    """Native page breaks when present; otherwise soft-paginate like paste."""
    doc = DocxDocument(io.BytesIO(data))
    hard_segments: list[str] = []
    current: list[str] = []
    native_breaks = 0

    def flush(*, from_break: bool = False) -> None:
        nonlocal native_breaks
        hard_segments.append("\n\n".join(part for part in current if part.strip()).strip())
        current.clear()
        if from_break:
            native_breaks += 1

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
            flush(from_break=True)
        parts = _split_paragraph_by_page_breaks(paragraph)
        for i, part in enumerate(parts):
            if i > 0:
                flush(from_break=True)
            add_text(part)

    flush(from_break=False)
    while len(hard_segments) > 1 and not hard_segments[-1]:
        hard_segments.pop()
    return study_pages_from_native_units(hard_segments, has_native=native_breaks > 0)


def _pptx_slide_targets(zf: ZipFile) -> list[str]:
    """Slide paths in presentation order from presentation.xml + rels."""
    try:
        rels_root = ET.fromstring(zf.read("ppt/_rels/presentation.xml.rels"))
    except KeyError:
        return sorted(
            n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)
        )
    id_to_target: dict[str, str] = {}
    for rel in rels_root.findall("r:Relationship", _R_NS):
        typ = rel.attrib.get("Type", "")
        if typ.endswith("/slide"):
            target = rel.attrib.get("Target", "")
            rid = rel.attrib.get("Id", "")
            if target and rid:
                id_to_target[rid] = (
                    target if target.startswith("ppt/") else f"ppt/{target.lstrip('/')}"
                )
    try:
        pres = ET.fromstring(zf.read("ppt/presentation.xml"))
    except KeyError:
        return list(id_to_target.values())
    ordered: list[str] = []
    for sld in pres.findall(".//p:sldIdLst/p:sldId", _P_NS):
        rid = sld.attrib.get(f"{_PR_NS}id") or sld.attrib.get("r:id")
        if rid and rid in id_to_target:
            ordered.append(id_to_target[rid])
    return ordered or list(id_to_target.values())


def _pptx_slide_text(xml_bytes: bytes) -> str:
    root = ET.fromstring(xml_bytes)
    parts: list[str] = []
    for node in root.findall(".//a:t", _A_NS):
        if node.text and node.text.strip():
            parts.append(node.text.strip())
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _parse_pptx(data: bytes) -> list[dict]:
    """One PowerPoint slide = one native study page."""
    try:
        with ZipFile(io.BytesIO(data)) as zf:
            targets = _pptx_slide_targets(zf)
            units: list[str] = []
            for path in targets:
                try:
                    units.append(_pptx_slide_text(zf.read(path)))
                except KeyError:
                    units.append("")
    except Exception:
        return [{"page": 1, "text": ""}]
    return study_pages_from_native_units(units or [""], has_native=True)


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
                # Pre-baked article pages (paste/URL/YouTube) are native units.
                units = [
                    str(item.get("text") or "").strip()
                    for item in pages
                    if isinstance(item, dict)
                ]
                units = [u for u in units if u]
                if units:
                    return study_pages_from_native_units(units, has_native=True)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    # Plain text / markdown — no native pages → soft fallback.
    return paginate_reader_text(text.strip())
