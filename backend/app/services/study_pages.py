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

from app.engine_runtime import pick
from app.services.content_worthiness import (
    plan_study_page_split,
    should_split_native_unit,
)
from app.services.web_import import paginate_reader_text

NATIVE_SOFT_SPLIT_CHARS = plan_study_page_split().native_split_chars


def study_pages_from_native_units(
    units: list[str],
    *,
    has_native: bool,
) -> list[dict[str, Any]]:
    """Build ``[{page, text}, …]`` from native units or soft-fallback the blob.

    ``has_native`` must be True only when the format actually contributed page /
    slide / break boundaries (not merely “we flushed one leftover segment”).
    """
    cleaned = list(filter(None, map(lambda u: str(u).strip(), filter(None, units))))

    def _from_native() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page_num = 1

        def _append(text: str) -> None:
            nonlocal page_num
            out.append({"page": page_num, "text": text})
            page_num += 1

        def _soft_split(unit: str) -> None:
            for item in paginate_reader_text(unit):
                _append(item["text"])

        for unit in cleaned:
            pick(not should_split_native_unit(unit), lambda: _append(unit), lambda: _soft_split(unit))
        return out or [{"page": 1, "text": ""}]

    return pick(
        not cleaned,
        lambda: [{"page": 1, "text": ""}],
        lambda: pick(not has_native, lambda: paginate_reader_text("\n\n".join(cleaned)), _from_native),
    )
