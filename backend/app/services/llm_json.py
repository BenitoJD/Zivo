"""Tolerant JSON extraction for LLM output.

LLMs routinely wrap JSON in ``` fences or surround it with prose; these helpers
strip that and salvage a parseable object/array. Used by services that consume
JSON-shaped model output (resume, interview, …).
"""

from __future__ import annotations

import json
import re
from typing import Any


def extract_json_obj(raw: str) -> dict[str, Any]:
    """Tolerant single-object JSON parse (handles code fences / surrounding prose)."""
    if not raw or not raw.strip():
        return {}
    s = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.DOTALL)
    if fence:
        s = fence.group(1)
    else:
        a, b = s.find("{"), s.rfind("}")
        if a >= 0 and b > a:
            s = s[a : b + 1]
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def extract_json_array(raw: str) -> list[dict[str, Any]]:
    """Tolerant JSON-array parse (handles code fences / surrounding prose).

    On a hard JSONDecodeError (typically provider truncation), salvages every
    complete ``{...}`` object from the raw text so a partially-truncated array
    still yields the objects that did finish. Callers then coerce each dict to
    their domain shape (topic / card / question / …).
    """
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.DOTALL)
    candidate = fence.group(1) if fence else text
    if not fence:
        start, end = text.find("["), text.rfind("]")
        if start >= 0 and end > start:
            candidate = text[start : end + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        # Salvage every complete {...} object from the full text (not the
        # bracket-sliced candidate, whose end can land on an inner `]`).
        data = []
        for frag in re.findall(r"\{[^{}]*\}", text, re.DOTALL):
            try:
                obj = json.loads(frag)
                if isinstance(obj, dict):
                    data.append(obj)
            except json.JSONDecodeError:
                continue
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]

