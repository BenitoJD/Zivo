"""Tolerant JSON extraction for LLM output.

LLMs routinely wrap JSON in ``` fences or surround it with prose; these helpers
strip that and salvage a parseable object/array. Used by services that consume
JSON-shaped model output (resume, interview, …).
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.engine_runtime import pick


def extract_json_obj(raw: str) -> dict[str, Any]:
    """Tolerant single-object JSON parse (handles code fences / surrounding prose)."""

    def _parse() -> dict[str, Any]:
        s = raw.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.DOTALL)

        def _from_braces() -> str:
            a, b = s.find("{"), s.rfind("}")
            return pick(a >= 0 and b > a, lambda: s[a : b + 1], lambda: s)

        s = pick(bool(fence), lambda: fence.group(1), _from_braces)
        try:
            obj = json.loads(s)
            return pick(isinstance(obj, dict), lambda: obj, lambda: {})
        except json.JSONDecodeError:
            return {}

    return pick(not raw or not raw.strip(), lambda: {}, _parse)


def extract_json_array(raw: str) -> list[dict[str, Any]]:
    """Tolerant JSON-array parse (handles code fences / surrounding prose).

    On a hard JSONDecodeError (typically provider truncation), salvages every
    complete ``{...}`` object from the raw text so a partially-truncated array
    still yields the objects that did finish. Callers then coerce each dict to
    their domain shape (topic / card / question / …).
    """

    def _parse() -> list[dict[str, Any]]:
        text = raw.strip()
        fence = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.DOTALL)
        candidate = pick(bool(fence), lambda: fence.group(1), lambda: text)

        def _from_brackets() -> str:
            start, end = text.find("["), text.rfind("]")
            return pick(start >= 0 and end > start, lambda: text[start : end + 1], lambda: candidate)

        candidate = pick(not fence, _from_brackets, lambda: candidate)
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            data = []
            for frag in re.findall(r"\{[^{}]*\}", text, re.DOTALL):
                try:
                    obj = json.loads(frag)
                    pick(isinstance(obj, dict), lambda: data.append(obj), lambda: None)
                except json.JSONDecodeError:
                    continue
        return pick(
            not isinstance(data, list),
            lambda: [],
            lambda: list(filter(lambda d: isinstance(d, dict), data)),
        )

    return pick(not raw or not raw.strip(), lambda: [], _parse)
