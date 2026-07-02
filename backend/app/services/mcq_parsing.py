"""MCQ response parsing + normalization helpers (pure, no LLM/DB).

Extracted from mcq_quality.py: turning raw model output into structured MCQ
payloads — JSON/fenced-block extraction, critic-JSON parsing, explanation
trimming, payload normalization, and tested-concept coercion. Leaf module: the
generation/critic pipeline in mcq_quality imports from here, not vice versa.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.services.mcq_dedup import (
    coerce_mcq_options,
    sanitize_mcq_explanation,
    sanitize_mcq_stem,
)


def _parse_mcq_json(raw: str) -> dict[str, Any] | None:
    if not raw or not raw.strip():
        return None
    text_block = raw.strip()
    fence = re.search(r"```(?:zv-mcq|json)?\s*(\{.*?\})\s*```", text_block, re.DOTALL)
    if fence:
        text_block = fence.group(1)
    elif not text_block.startswith("{"):
        start = text_block.find("{")
        end = text_block.rfind("}")
        if start >= 0 and end > start:
            text_block = text_block[start : end + 1]
        else:
            return None
    try:
        return json.loads(text_block)
    except json.JSONDecodeError:
        return None


def _parse_critic_json(raw: str) -> dict[str, Any] | None:
    return _parse_mcq_json(raw)


# Fenced-block splitter: captures the JSON inside each ```zv-mcq ... ``` block.
# Non-greedy on the inner JSON (each MCQ block is flat, so this is safe) and
# finds ALL blocks rather than just the first. Used by one-call batch generation.
_FENCED_MCQ_RE = re.compile(r"```(?:zv-mcq|json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_mcq_blocks(raw: str) -> list[dict[str, Any]]:
    """Extract every MCQ JSON object from a multi-block LLM response.

    Handles fenced ```zv-mcq blocks (the normal case) and falls back to scanning
    for top-level JSON objects if the model omitted fences. Each candidate is
    json.loads'd; unparseable candidates are skipped individually so one bad
    block never fails the whole batch.
    """
    if not raw or not raw.strip():
        return []
    text_block = raw.strip()
    out: list[dict[str, Any]] = []

    # Primary path: fenced blocks.
    for match in _FENCED_MCQ_RE.finditer(text_block):
        try:
            obj = json.loads(match.group(1))
            if isinstance(obj, dict):
                out.append(obj)
        except json.JSONDecodeError:
            continue

    if out:
        return out

    # Fallback: the model returned a bare JSON array of MCQ objects, or a run of
    # brace-delimited objects without fences. Try a JSON array first, then a
    # whole-text parse, then a brace scan.
    if text_block.startswith("["):
        try:
            arr = json.loads(text_block)
            if isinstance(arr, list):
                return [o for o in arr if isinstance(o, dict)]
        except json.JSONDecodeError:
            pass

    if text_block.startswith("{"):
        try:
            obj = json.loads(text_block)
            if isinstance(obj, dict):
                return [obj]
        except json.JSONDecodeError:
            pass

    # Last resort: pull each top-level {...} span. Greedy-with-balancing isn't
    # trivial with regex, so we find each '{'...'{' pair at the shallowest level
    # via a simple depth scan — robust enough for flat MCQ objects.
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text_block):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    obj = json.loads(text_block[start : i + 1])
                    if isinstance(obj, dict):
                        out.append(obj)
                except json.JSONDecodeError:
                    pass
                start = -1
    return out


def _normalize_mcq_payload(data: dict[str, Any], target_aspect: dict[str, Any] | None) -> dict[str, Any]:
    question = sanitize_mcq_stem(str(data.get("question") or data.get("stem") or ""))
    options = coerce_mcq_options(data.get("options") or data.get("choices"))
    # No length cap — the learner needs the whole explanation to understand the
    # answer; we only strip document/page framing.
    explanation = sanitize_mcq_explanation(str(data.get("explanation") or ""))
    if not question or len(options) < 2:
        raise ValueError("invalid mcq")
    key = data.get("primary_concept_key") or (target_aspect or {}).get("key") or "page-concept"
    label = data.get("primary_concept") or (target_aspect or {}).get("label") or "Page concept"
    # Exam-style metadata rides along when the generator emits it (question_style /
    # difficulty / cognitive_level) — informative, never required.
    meta = {
        k: str(data[k]).strip().lower()[:40]
        for k in ("question_style", "difficulty", "cognitive_level")
        if str(data.get(k) or "").strip()
    }
    # Multi-select ("select all that apply" / "select TWO") items carry
    # correct_indices; single-best-answer items carry correct_index. We always set
    # correct_index (= first correct) for back-compat with every single-answer
    # consumer, and add correct_indices only when the item is genuinely multi.
    correct_indices = _coerce_correct_indices(data.get("correct_indices"), len(options))
    if correct_indices is not None and len(correct_indices) >= 2:
        meta["correct_indices"] = correct_indices
        correct_index = correct_indices[0]
    else:
        correct_index = int(data.get("correct_index", 0))
    return {
        **meta,
        "question": question,
        "options": options,
        "correct_index": correct_index,
        "explanation": explanation,
        "primary_concept_key": key,
        "primary_concept": label,
        "tags": data.get("tags") or ["auto"],
        # Wikidata concept tags — the LLM emits these from the source material;
        # persist-time code in generation_graph.py links them to intel.entity rows.
        "tested_concepts": _coerce_tested_concepts(data.get("tested_concepts")),
    }


def _coerce_correct_indices(raw: Any, n_options: int) -> list[int] | None:
    """Normalize a multi-select answer key to a sorted, de-duped, in-range list.

    Returns None when the field is absent/unusable — the item is then treated as
    single-best-answer via correct_index.
    """
    if not isinstance(raw, list):
        return None
    out: list[int] = []
    for v in raw:
        try:
            i = int(v)
        except (TypeError, ValueError):
            continue
        if 0 <= i < n_options and i not in out:
            out.append(i)
    return sorted(out) or None


# Shape: [{"qid": "Q80001", "label": "Photosynthesis"}]
_QID_RE = re.compile(r"^Q\d+$", re.IGNORECASE)


def _coerce_tested_concepts(raw: Any) -> list[dict[str, str]]:
    """Normalize the model's tested_concepts output to [{qid, label}]."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("qid") or "").strip()
        if not _QID_RE.match(qid):
            continue
        qid = qid.upper()
        if qid in seen:
            continue
        seen.add(qid)
        label = str(item.get("label") or "").strip()[:200]
        if not label:
            continue
        out.append({"qid": qid, "label": label})
    return out
