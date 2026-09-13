"""Document parsing — PDF, DOCX, PPTX, plain text, imported articles.

Study pages follow a native-first policy (see ``app.services.study_pages``):
use the format's own page/slide/break units when present; soft-paginate only
when the format has no native paging.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pymupdf
from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.services.content_worthiness import is_sparse_page_text, plan_stored_page_count_trust
from app.services.parse_detect import evaluate_parse_detect
from app.services.presence import evaluate_presence
from app.services.session_design import plan_study_range_heal
from app.services.study_pages import study_pages_from_native_units
from app.services.web_import import paginate_reader_text

_PPTX_CT = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_A_NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
_P_NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main"}
_R_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
_PR_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

_PARSE_RULES = (
    Rule(when=(Pred("fmt", "eq", "pdf"),), action="pdf"),
    Rule(when=(Pred("pdf_ct", "truthy"),), action="pdf"),
    Rule(when=(Pred("docx_ct", "truthy"),), action="docx"),
    Rule(when=(Pred("pptx_ct", "truthy"),), action="pptx"),
    Rule(when=(Pred("json_ct", "truthy"),), action="text"),
    Rule(when=(Pred("xml", "truthy"),), action="xml"),
    Rule(when=(Pred("text_ct", "truthy"),), action="text"),
    Rule(when=(), action="placeholder"),
)


def document_bytes_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_document(content_type: str, data: bytes) -> list[dict]:
    """Return list of {page: int, text: str} (1-indexed study pages)."""
    ct = (content_type or "").lower()
    detect = evaluate_parse_detect(header=data[:8])
    hit = first_match(
        _PARSE_RULES,
        {
            "fmt": detect.action,
            "pdf_ct": "pdf" in ct,
            "docx_ct": "wordprocessingml" in ct or "msword" in ct,
            "pptx_ct": "presentationml" in ct or ct == _PPTX_CT,
            "json_ct": ct == "application/json",
            "xml": _looks_like_xml(data),
            "text_ct": ct.startswith("text/"),
        },
    )
    return apply(
        hit.action,
        {
            "pdf": lambda: _parse_pdf(data),
            "docx": lambda: _parse_docx(data),
            "pptx": lambda: _parse_pptx(data),
            "xml": lambda: _parse_xml(data),
            "text": lambda: _parse_text(data),
            "placeholder": lambda: [{"page": 1, "text": ""}],
        },
    )


def _block_piece(block: object) -> str:
    return pick(
        isinstance(block, (list, tuple)) and len(block) > 4,
        lambda: str(block[4]).strip(),
        lambda: "",
    )


def _page_text(page: object) -> str:
    text = page.get_text("text").strip()

    def _from_blocks() -> str:
        block_text = "\n".join(filter(None, map(_block_piece, page.get_text("blocks")))).strip()
        return choose(len(block_text) > len(text), block_text, text)

    return pick(is_sparse_page_text(text), _from_blocks, lambda: text)


def _pdf_entry(i: int, page: object) -> dict:
    text = _page_text(page)
    entry: dict = {"page": i, "text": text}
    pick(is_sparse_page_text(text), lambda: entry.__setitem__("sparse_text", True), lambda: None)
    return entry


def _parse_pdf(data: bytes) -> list[dict]:
    """PDF physical pages are native units — never soft-merge them."""
    pages: list[dict] = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        pages.extend(_pdf_entry(i, page) for i, page in enumerate(doc, start=1))
    return pages or [{"page": 1, "text": "", "sparse_text": True}]


def parse_pdf_page(data: bytes, page_number: int) -> dict:
    """Extract text for a single 1-indexed PDF page."""
    page_number = max(1, int(page_number))
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return pick(
            page_number > doc.page_count,
            lambda: {"page": page_number, "text": ""},
            lambda: {"page": page_number, "text": doc[page_number - 1].get_text("text").strip()},
        )


def _page_from_item(item: dict, page_number: int) -> dict:
    return {"page": page_number, "text": str(item.get("text") or "").strip()}


def parse_document_page(
    content_type: str,
    data: bytes,
    page_number: int,
    *,
    cached_pages: list[dict] | None = None,
) -> dict:
    """Return {page, text} for one study page."""
    page_number = int(page_number)

    def _from_cache() -> dict:
        hit = next(
            filter(lambda item: int(item.get("page", 0)) == page_number, cached_pages),
            None,
        )
        return apply(
            evaluate_presence(hit).action,
            {
                "missing": lambda: {"page": page_number, "text": ""},
                "empty": lambda: {"page": page_number, "text": ""},
                "ok": lambda: _page_from_item(hit, page_number),
            },
        )

    def _from_pages(pages: list[dict]) -> dict:
        return pick(
            len(pages) == 1,
            lambda: pages[0],
            lambda: next(
                filter(lambda item: int(item.get("page", 0)) == page_number, pages),
                {"page": page_number, "text": ""},
            ),
        )

    def _from_parse() -> dict:
        ct = (content_type or "").lower()
        detect = evaluate_parse_detect(header=data[:8])
        return pick(
            detect.action == "pdf" or "pdf" in ct,
            lambda: parse_pdf_page(data, page_number),
            lambda: _from_pages(parse_document(content_type, data)),
        )

    return apply(
        evaluate_presence(cached_pages).action,
        {
            "ok": _from_cache,
            "empty": _from_parse,
            "missing": _from_parse,
        },
    )


def count_pdf_pages(data: bytes) -> int:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return max(1, doc.page_count)


def count_document_pages(content_type: str, data: bytes) -> int:
    """Page count for upload meta — native units when present, else parse length."""
    ct = (content_type or "").lower()
    detect = evaluate_parse_detect(header=data[:8])
    return pick(
        detect.action == "pdf" or "pdf" in ct,
        lambda: count_pdf_pages(data),
        lambda: max(1, len(parse_document(content_type, data))),
    )


def refresh_document_page_count(db, doc) -> int:
    """Ensure ``meta.page_count`` matches the file (heals pre-soft-paginate DOCX).

    Cheap for PDF (pymupdf page count). For Word/text/paste, re-parses once when the
    stored count is missing or still ``1`` on a non-trivial file — those were the
    docs that got stuck as a single mega-page before soft pagination shipped.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.services.storage import fetch_object

    meta = dict(doc.meta or {})
    stored = meta.get("page_count")
    try:
        stored_i = pick(stored is None, lambda: None, lambda: int(stored))
    except (TypeError, ValueError):
        stored_i = None

    ct = (doc.content_type or "").lower()

    def _persist(counted: int) -> int:
        meta["page_count"] = counted
        selected = pick(
            isinstance(meta.get("selected_range"), dict),
            lambda: meta.get("selected_range"),
            lambda: None,
        )
        heal = plan_study_range_heal(
            counted_pages=counted,
            selected_range=selected,
            doc_status=str(getattr(doc, "status", "") or ""),
        )
        pick(heal.clear_selected_range, lambda: meta.pop("selected_range", None), lambda: None)

        def _reset() -> None:
            doc.status = "pending"
            doc.index_progress = 0

        pick(heal.reset_to_pending, _reset, lambda: None)
        doc.meta = meta
        flag_modified(doc, "meta")
        db.commit()
        return counted

    def _recount() -> int:
        try:
            data = fetch_object(doc.storage_key)
            counted = count_document_pages(doc.content_type, data)
        except Exception:
            return stored_i or 1
        return pick(counted == stored_i, lambda: counted or 1, lambda: _persist(counted))

    def _non_image() -> int:
        trust = plan_stored_page_count_trust(
            size_bytes=int(getattr(doc, "size_bytes", 0) or 0),
            stored_pages=stored_i,
        )
        return pick(trust.trust, lambda: trust.pages, _recount)

    return pick(ct.startswith("image/"), lambda: stored_i or 1, _non_image)


def render_pdf_page_png(
    data: bytes,
    page_number: int,
    *,
    max_edge: int = 1600,
    max_bytes: int = 4 * 1024 * 1024,
) -> bytes | None:
    """Render one 1-indexed PDF page to a size-capped PNG for vision models."""
    page_number = max(1, int(page_number))

    def _render_page(doc: object) -> bytes | None:
        page = doc[page_number - 1]
        rect = page.rect
        long_edge = max(float(rect.width), float(rect.height), 1.0)
        scale = min(2.0, max_edge / long_edge)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        png = pix.tobytes("png")
        while len(png) > max_bytes and scale > 0.35:
            scale *= 0.75
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            png = pix.tobytes("png")
        return choose(len(png) > max_bytes, None, png)

    def _render() -> bytes | None:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            return pick(
                page_number > doc.page_count,
                lambda: None,
                lambda: _render_page(doc),
            )

    try:
        return _render()
    except Exception:
        return None


def _iter_docx_blocks(doc: DocxDocument):
    """Yield paragraphs and tables in document body order."""
    body = doc.element.body
    for child in body.iterchildren():
        yield from apply(
            first_match(
                (
                    Rule(when=(Pred("tag", "eq", qn("w:p")),), action="p"),
                    Rule(when=(Pred("tag", "eq", qn("w:tbl")),), action="t"),
                    Rule(when=(), action="skip"),
                ),
                {"tag": child.tag},
            ).action,
            {
                "p": lambda c=child: (("p", Paragraph(c, doc)),),
                "t": lambda c=child: (("t", Table(c, doc)),),
                "skip": lambda: (),
            },
        )


def _table_text(table: Table) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells = list(filter(None, map(lambda cell: cell.text.strip(), row.cells)))
        pick(bool(cells), lambda c=cells: rows.append(" | ".join(c)), lambda: None)
    return "\n".join(rows)


def _paragraph_page_break_before(paragraph: Paragraph) -> bool:
    p_pr = paragraph._element.find(qn("w:pPr"))
    return p_pr is not None and p_pr.find(qn("w:pageBreakBefore")) is not None


def _split_runs(paragraph: Paragraph) -> list[str]:
    segments: list[str] = [""]
    for run in paragraph.runs:
        for child in run._element:
            apply(
                first_match(
                    (
                        Rule(when=(Pred("page_break", "truthy"),), action="break"),
                        Rule(when=(Pred("text", "truthy"),), action="text"),
                        Rule(when=(Pred("tab", "truthy"),), action="tab"),
                        Rule(when=(), action="skip"),
                    ),
                    {
                        "page_break": child.tag == qn("w:lastRenderedPageBreak")
                        or (child.tag == qn("w:br") and child.get(qn("w:type")) == "page"),
                        "text": child.tag == qn("w:t"),
                        "tab": child.tag == qn("w:tab"),
                    },
                ).action,
                {
                    "break": lambda: segments.append(""),
                    "text": lambda: segments.__setitem__(-1, segments[-1] + (child.text or "")),
                    "tab": lambda: segments.__setitem__(-1, segments[-1] + "\t"),
                    "skip": lambda: None,
                },
            )
    return segments


def _split_paragraph_by_page_breaks(paragraph: Paragraph) -> list[str]:
    """Split one paragraph on hard + Word-rendered page breaks; always ≥1 segment."""
    return pick(
        not paragraph.runs,
        lambda: [paragraph.text or ""],
        lambda: _split_runs(paragraph),
    )


def _parse_docx(data: bytes) -> list[dict]:
    """Native page breaks when present; otherwise soft-paginate like paste."""
    doc = DocxDocument(io.BytesIO(data))
    hard_segments: list[str] = []
    current: list[str] = []
    native_breaks = 0

    def flush(*, from_break: bool = False) -> None:
        nonlocal native_breaks
        hard_segments.append("\n\n".join(filter(None, current)).strip())
        current.clear()
        native_breaks += int(from_break)

    def add_text(text: str) -> None:
        stripped = text.strip()
        pick(bool(stripped), lambda: current.append(stripped), lambda: None)

    def _handle_paragraph(paragraph: Paragraph) -> None:
        pick(
            _paragraph_page_break_before(paragraph) and bool(current),
            lambda: flush(from_break=True),
            lambda: None,
        )
        parts = _split_paragraph_by_page_breaks(paragraph)
        for i, part in enumerate(parts):
            pick(i > 0, lambda: flush(from_break=True), lambda: None)
            add_text(part)

    for kind, block in _iter_docx_blocks(doc):
        apply(
            kind,
            {
                "t": lambda b=block: add_text(_table_text(b)),
                "p": lambda b=block: _handle_paragraph(b),
            },
        )

    flush(from_break=False)
    while len(hard_segments) > 1 and not hard_segments[-1]:
        hard_segments.pop()
    return study_pages_from_native_units(hard_segments, has_native=native_breaks > 0)


def _pptx_slide_targets(zf: ZipFile) -> list[str]:
    """Slide paths in presentation order from presentation.xml + rels."""
    try:
        rels_root = ET.fromstring(zf.read("ppt/_rels/presentation.xml.rels"))
    except KeyError:
        return sorted(filter(lambda n: re.match(r"ppt/slides/slide\d+\.xml$", n), zf.namelist()))
    id_to_target: dict[str, str] = {}
    for rel in rels_root.findall("r:Relationship", _R_NS):
        typ = rel.attrib.get("Type", "")
        target = rel.attrib.get("Target", "")
        rid = rel.attrib.get("Id", "")
        pick(
            typ.endswith("/slide") and bool(target) and bool(rid),
            lambda t=target, r=rid: id_to_target.__setitem__(
                r, choose(t.startswith("ppt/"), t, f"ppt/{t.lstrip('/')}")
            ),
            lambda: None,
        )
    try:
        pres = ET.fromstring(zf.read("ppt/presentation.xml"))
    except KeyError:
        return list(id_to_target.values())
    ordered: list[str] = []
    for sld in pres.findall(".//p:sldIdLst/p:sldId", _P_NS):
        rid = sld.attrib.get(f"{_PR_NS}id") or sld.attrib.get("r:id")
        pick(
            bool(rid) and rid in id_to_target,
            lambda r=rid: ordered.append(id_to_target[r]),
            lambda: None,
        )
    return ordered or list(id_to_target.values())


def _pptx_slide_text(xml_bytes: bytes) -> str:
    root = ET.fromstring(xml_bytes)
    parts: list[str] = []
    for node in root.findall(".//a:t", _A_NS):
        pick(
            bool(node.text and node.text.strip()),
            lambda n=node: parts.append(n.text.strip()),
            lambda: None,
        )
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


def _looks_like_xml(data: bytes) -> bool:
    head = data.lstrip()[:200]
    return head.startswith(b"<?xml") or (head.startswith(b"<") and b">" in head[:80])


def _xml_element_text(node: ET.Element) -> str:
    parts: list[str] = []
    pick(bool(node.text and node.text.strip()), lambda: parts.append(node.text.strip()), lambda: None)
    for child in node:
        child_text = _xml_element_text(child)
        pick(bool(child_text), lambda t=child_text: parts.append(t), lambda: None)
        pick(
            bool(child.tail and child.tail.strip()),
            lambda c=child: parts.append(c.tail.strip()),
            lambda: None,
        )
    return " ".join(parts)


def _parse_xml(data: bytes) -> list[dict]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    try:
        root = ET.fromstring(text)
        body = _xml_element_text(root).strip()
        return pick(bool(body), lambda: paginate_reader_text(body), lambda: paginate_reader_text(text.strip()))
    except ET.ParseError:
        return paginate_reader_text(text.strip())


def _pages_from_json(payload: object) -> list[dict] | None:
    pages = payload.get("pages")

    def _units() -> list[dict] | None:
        units = list(
            filter(
                None,
                map(
                    lambda item: pick(
                        isinstance(item, dict),
                        lambda i=item: str(i.get("text") or "").strip(),
                        lambda: "",
                    ),
                    pages,
                ),
            )
        )
        return pick(
            bool(units),
            lambda: study_pages_from_native_units(units, has_native=True),
            lambda: None,
        )

    return pick(
        isinstance(pages, list) and bool(pages),
        _units,
        lambda: None,
    )


def _parse_json_text(text: str, data: bytes) -> list[dict]:
    try:
        payload = json.loads(text)
        baked = _pages_from_json(payload)
        return apply(
            evaluate_presence(baked).action,
            {
                "ok": lambda: baked,
                "missing": lambda: paginate_reader_text(
                    json.dumps(payload, indent=2, ensure_ascii=False)
                ),
                "empty": lambda: paginate_reader_text(
                    json.dumps(payload, indent=2, ensure_ascii=False)
                ),
            },
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        return pick(_looks_like_xml(data), lambda: _parse_xml(data), lambda: paginate_reader_text(text.strip()))


def _parse_text(data: bytes) -> list[dict]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    stripped = text.lstrip()
    return pick(
        stripped.startswith("{"),
        lambda: _parse_json_text(text, data),
        lambda: pick(
            _looks_like_xml(data),
            lambda: _parse_xml(data),
            lambda: paginate_reader_text(text.strip()),
        ),
    )
