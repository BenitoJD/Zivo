"""Study-page units — prefer native paging/structure, soft-paginate only as fallback.

Policy (all input formats):
1. If the format exposes a native unit (PDF page, PPTX slide, DOCX hard/rendered
   page break, pre-baked article ``pages`` JSON), those units ARE the study pages.
2. Soft character/heading pagination runs only when no native units exist.
3. Safety: a single native unit larger than ``NATIVE_SOFT_SPLIT_CHARS`` is still
   soft-split so one giant Word page cannot recreate a mega-page quiz plan.
"""

from __future__ import annotations

from typing import Any

from app.services.web_import import READER_PAGE_CHARS, paginate_reader_text

# Allow a native page/slide to be ~2× the soft budget before we subdivide it.
NATIVE_SOFT_SPLIT_CHARS = READER_PAGE_CHARS * 2


def study_pages_from_native_units(
    units: list[str],
    *,
    has_native: bool,
) -> list[dict[str, Any]]:
    """Build ``[{page, text}, …]`` from native units or soft-fallback the blob.

    ``has_native`` must be True only when the format actually contributed page /
    slide / break boundaries (not merely “we flushed one leftover segment”).
    """
    cleaned = [u.strip() for u in units if u and str(u).strip()]
    if not cleaned:
        return [{"page": 1, "text": ""}]

    if not has_native:
        return paginate_reader_text("\n\n".join(cleaned))

    out: list[dict[str, Any]] = []
    page_num = 1
    for unit in cleaned:
        if len(unit) <= NATIVE_SOFT_SPLIT_CHARS:
            out.append({"page": page_num, "text": unit})
            page_num += 1
            continue
        # Native unit is enormous — soft-split inside it, keep order.
        for item in paginate_reader_text(unit):
            out.append({"page": page_num, "text": item["text"]})
            page_num += 1
    return out or [{"page": 1, "text": ""}]
