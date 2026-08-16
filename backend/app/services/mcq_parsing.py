"""MCQ response parsing + normalization helpers (pure, no LLM/DB).

Extracted from mcq_quality.py: turning raw model output into structured MCQ
payloads — JSON/fenced-block extraction, critic-JSON parsing, explanation
trimming, payload normalization, and tested-concept coercion. Leaf module: the
generation/critic pipeline in mcq_quality imports from here, not vice versa.
"""

from __future__ import annotations

import json
import random
import re
from typing import Any

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.services.mcq_dedup import (
    coerce_mcq_options,
    sanitize_mcq_explanation,
    sanitize_mcq_stem,
)
from app.services.presence import evaluate_presence

_PREPARE_RULES = (
    Rule(when=(Pred("fenced", "truthy"),), action="fence"),
    Rule(when=(Pred("starts_obj", "truthy"),), action="as_is"),
    Rule(when=(), action="slice"),
)

_BLOCK_FALLBACK_RULES = (
    Rule(when=(Pred("has_fenced", "truthy"),), action="fenced"),
    Rule(when=(Pred("starts_arr", "truthy"), Pred("arr_ok", "truthy")), action="array"),
    Rule(when=(Pred("starts_obj", "truthy"), Pred("obj_ok", "truthy")), action="object"),
    Rule(when=(), action="scan"),
)

_SCAN_RULES = (
    Rule(when=(Pred("escape", "truthy"),), action="clear_escape"),
    Rule(when=(Pred("backslash", "truthy"),), action="set_escape"),
    Rule(when=(Pred("quote", "truthy"),), action="toggle_string"),
    Rule(when=(Pred("in_string", "truthy"),), action="skip"),
    Rule(when=(Pred("open_brace", "truthy"),), action="open"),
    Rule(when=(Pred("close_brace", "truthy"),), action="close"),
    Rule(when=(), action="skip"),
)

_SHUFFLE_RULES = (
    Rule(when=(Pred("too_few", "truthy"),), action="keep"),
    Rule(when=(), action="shuffle"),
)


def _loads_dict(text_block: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(text_block)
    except json.JSONDecodeError:
        return None
    return pick(isinstance(obj, dict), lambda: obj, lambda: None)


def _slice_braces(text_block: str) -> str | None:
    start = text_block.find("{")
    end = text_block.rfind("}")
    return pick(
        start >= 0 and end > start,
        lambda: text_block[start : end + 1],
        lambda: None,
    )


def _parse_mcq_json(raw: str) -> dict[str, Any] | None:
    def parse_body() -> dict[str, Any] | None:
        text_block = raw.strip()
        fence = re.search(r"```(?:zv-mcq|json)?\s*(\{.*?\})\s*```", text_block, re.DOTALL)
        hit = first_match(
            _PREPARE_RULES,
            {"fenced": bool(fence), "starts_obj": text_block.startswith("{")},
        )
        prepared = apply(
            hit.action,
            {
                "fence": lambda: fence.group(1),
                "as_is": lambda: text_block,
                "slice": lambda: _slice_braces(text_block),
            },
        )
        return pick(prepared is None, lambda: None, lambda: _loads_dict(prepared))

    return pick(
        evaluate_presence((raw or "").strip()).action != "ok",
        lambda: None,
        parse_body,
    )


def _parse_critic_json(raw: str) -> dict[str, Any] | None:
    return _parse_mcq_json(raw)


# Fenced-block splitter: captures the JSON inside each ```zv-mcq ... ``` block.
# Non-greedy on the inner JSON (each MCQ block is flat, so this is safe) and
# finds ALL blocks rather than just the first. Used by one-call batch generation.
_FENCED_MCQ_RE = re.compile(r"```(?:zv-mcq|json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _try_json(text_block: str) -> Any | None:
    try:
        return json.loads(text_block)
    except json.JSONDecodeError:
        return None


def _maybe_append_dict(out: list[dict[str, Any]], obj: Any) -> None:
    pick(isinstance(obj, dict), lambda: out.append(obj), lambda: None)


def _scan_brace_objects(text_block: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    state = {"depth": 0, "start": -1, "in_string": False, "escape": False}

    def handle(i: int, ch: str) -> None:
        hit = first_match(
            _SCAN_RULES,
            {
                "escape": state["escape"],
                "backslash": ch == "\\",
                "quote": ch == '"',
                "in_string": state["in_string"],
                "open_brace": ch == "{",
                "close_brace": ch == "}",
            },
        )

        def clear_escape() -> None:
            state["escape"] = False

        def set_escape() -> None:
            state["escape"] = True

        def toggle_string() -> None:
            state["in_string"] = not state["in_string"]

        def open_brace() -> None:
            pick(state["depth"] == 0, lambda: state.__setitem__("start", i), lambda: None)
            state["depth"] += 1

        def close_brace() -> None:
            state["depth"] -= 1

            def flush() -> None:
                parsed = _try_json(text_block[state["start"] : i + 1])
                _maybe_append_dict(out, parsed)
                state["start"] = -1

            pick(state["depth"] == 0 and state["start"] >= 0, flush, lambda: None)

        apply(
            hit.action,
            {
                "clear_escape": clear_escape,
                "set_escape": set_escape,
                "toggle_string": toggle_string,
                "open": open_brace,
                "close": close_brace,
                "skip": lambda: None,
            },
        )

    for i, ch in enumerate(text_block):
        handle(i, ch)
    return out


def _parse_mcq_blocks(raw: str) -> list[dict[str, Any]]:
    """Extract every MCQ JSON object from a multi-block LLM response.

    Handles fenced ```zv-mcq blocks (the normal case) and falls back to scanning
    for top-level JSON objects if the model omitted fences. Each candidate is
    json.loads'd; unparseable candidates are skipped individually so one bad
    block never fails the whole batch.
    """

    def parse_body() -> list[dict[str, Any]]:
        text_block = raw.strip()
        out: list[dict[str, Any]] = []
        for match in _FENCED_MCQ_RE.finditer(text_block):
            parsed = _try_json(match.group(1))
            _maybe_append_dict(out, parsed)
        arr = pick(text_block.startswith("["), lambda: _try_json(text_block), lambda: None)
        obj = pick(text_block.startswith("{"), lambda: _try_json(text_block), lambda: None)
        hit = first_match(
            _BLOCK_FALLBACK_RULES,
            {
                "has_fenced": bool(out),
                "starts_arr": text_block.startswith("["),
                "arr_ok": isinstance(arr, list),
                "starts_obj": text_block.startswith("{"),
                "obj_ok": isinstance(obj, dict),
            },
        )
        return apply(
            hit.action,
            {
                "fenced": lambda: out,
                "array": lambda: list(filter(lambda o: isinstance(o, dict), arr)),
                "object": lambda: [obj],
                "scan": lambda: _scan_brace_objects(text_block),
            },
        )

    return pick(
        evaluate_presence((raw or "").strip()).action != "ok",
        lambda: [],
        parse_body,
    )


def _normalize_mcq_payload(data: dict[str, Any], target_aspect: dict[str, Any] | None) -> dict[str, Any]:
    question = sanitize_mcq_stem(str(data.get("question") or data.get("stem") or ""))
    options = coerce_mcq_options(data.get("options") or data.get("choices"))
    # No length cap — the learner needs the whole explanation to understand the
    # answer; we only strip document/page framing.
    explanation = sanitize_mcq_explanation(str(data.get("explanation") or ""))

    def invalid() -> dict[str, Any]:
        raise ValueError("invalid mcq")

    def build() -> dict[str, Any]:
        key = data.get("primary_concept_key") or (target_aspect or {}).get("key") or "page-concept"
        from app.services.mcq_dedup import short_concept_label

        label = short_concept_label(
            data.get("primary_concept") or (target_aspect or {}).get("label") or "Page concept"
        )
        meta_keys = filter(
            lambda k: str(data.get(k) or "").strip(),
            ("question_style", "difficulty", "cognitive_level"),
        )
        meta = {k: str(data[k]).strip().lower()[:40] for k in meta_keys}
        # Multi-select ("select all that apply" / "select TWO") items carry
        # correct_indices; single-best-answer items carry correct_index. We always set
        # correct_index (= first correct) for back-compat with every single-answer
        # consumer, and add correct_indices only when the item is genuinely multi.
        correct_indices = _coerce_correct_indices(data.get("correct_indices"), len(options))

        def as_multi() -> int:
            meta["correct_indices"] = correct_indices
            return correct_indices[0]

        correct_index = pick(
            correct_indices is not None and len(correct_indices) >= 2,
            as_multi,
            lambda: int(data.get("correct_index", 0)),
        )
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

    return pick(not question or len(options) < 2, invalid, build)


def shuffle_mcq_option_order(
    mcq: dict[str, Any],
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Randomize option slots after the quality gate so the keyed answer is not
    always in the same position (models often default to index 0 or 1)."""
    options = [str(o) for o in (mcq.get("options") or [])]
    n = len(options)
    hit = first_match(_SHUFFLE_RULES, {"too_few": n < 2})

    def shuffle_body() -> dict[str, Any]:
        order = list(range(n))
        mixer = rng or random.Random()
        mixer.shuffle(order)
        old_to_new = {old: new for new, old in enumerate(order)}
        shuffled_options = [options[i] for i in order]
        try:
            old_correct = int(mcq.get("correct_index", 0))
        except (TypeError, ValueError):
            old_correct = 0
        old_correct = choose(old_correct not in old_to_new, 0, old_correct)
        out = dict(mcq)
        out["options"] = shuffled_options
        out["correct_index"] = old_to_new[old_correct]
        raw_multi = mcq.get("correct_indices")

        def remap_multi() -> None:
            new_multi: list[int] = []

            def remap_one(raw_i: Any) -> None:
                try:
                    old_i = int(raw_i)
                except (TypeError, ValueError):
                    return
                pick(
                    old_i in old_to_new,
                    lambda: new_multi.append(old_to_new[old_i]),
                    lambda: None,
                )

            for raw_i in raw_multi:
                remap_one(raw_i)

            def apply_multi() -> None:
                out["correct_indices"] = sorted(set(new_multi))
                out["correct_index"] = out["correct_indices"][0]

            pick(len(new_multi) >= 2, apply_multi, lambda: None)

        pick(
            isinstance(raw_multi, list) and len(raw_multi) >= 2,
            remap_multi,
            lambda: None,
        )
        return out

    return apply(hit.action, {"keep": lambda: mcq, "shuffle": shuffle_body})


def _coerce_correct_indices(raw: Any, n_options: int) -> list[int] | None:
    """Normalize a multi-select answer key to a sorted, de-duped, in-range list.

    Returns None when the field is absent/unusable — the item is then treated as
    single-best-answer via correct_index.
    """

    def coerce_list() -> list[int] | None:
        out: list[int] = []

        def add_one(v: Any) -> None:
            try:
                i = int(v)
            except (TypeError, ValueError):
                return
            pick(0 <= i < n_options and i not in out, lambda: out.append(i), lambda: None)

        for v in raw:
            add_one(v)
        return sorted(out) or None

    return pick(not isinstance(raw, list), lambda: None, coerce_list)


# Shape: [{"qid": "Q80001", "label": "Photosynthesis"}]
_QID_RE = re.compile(r"^Q\d+$", re.IGNORECASE)


def _coerce_tested_concepts(raw: Any) -> list[dict[str, str]]:
    """Normalize the model's tested_concepts output to [{qid, label}]."""

    def coerce_list() -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        seen: set[str] = set()

        def consider(item: Any) -> None:
            def with_dict() -> None:
                qid_raw = str(item.get("qid") or "").strip()

                def with_qid() -> None:
                    qid = qid_raw.upper()

                    def fresh() -> None:
                        seen.add(qid)
                        label = str(item.get("label") or "").strip()[:200]
                        pick(
                            bool(label),
                            lambda: out.append({"qid": qid, "label": label}),
                            lambda: None,
                        )

                    pick(qid in seen, lambda: None, fresh)

                pick(not _QID_RE.match(qid_raw), lambda: None, with_qid)

            pick(not isinstance(item, dict), lambda: None, with_dict)

        for item in raw:
            consider(item)
        return out

    return pick(not isinstance(raw, list), lambda: [], coerce_list)

