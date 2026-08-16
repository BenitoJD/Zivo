"""Per-page MCQ dedup — aspect clustering, stem normalization, embedding similarity."""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.services.aspect_discovery import ASPECT_CLUSTER_THRESHOLD
from app.services.embed import embed_texts
from app.services.kc_coverage import (
    CONCEPT_LABEL_MAX_CHARS,
    normalize_concept_label,
)
from app.services.presence import evaluate_presence

# MCQ threshold lives on Quality Evaluation; resolved lazily to avoid
# mcq_dedup → quality_evaluation → mcq_heuristics → mcq_dedup cycle.
_SIGNATURE_EMBED_CACHE_MAX = 2_048

_ATTR_RULES = (
    Rule(when=(Pred("name", "eq", "MCQ_SIMILARITY_THRESHOLD"),), action="threshold"),
    Rule(when=(), action="missing"),
)

_COERCE_RULES = (
    Rule(when=(Pred("is_list", "truthy"),), action="list"),
    Rule(when=(Pred("is_dict", "truthy"),), action="dict"),
    Rule(when=(), action="empty"),
)


def __getattr__(name: str) -> float:
    hit = first_match(_ATTR_RULES, {"name": name})

    def threshold() -> float:
        from app.services.quality_evaluation import MCQ_SIMILARITY_THRESHOLD as value

        return value

    def missing() -> float:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    return apply(hit.action, {"threshold": threshold, "missing": missing})


_signature_embed_cache: OrderedDict[str, list[float]] = OrderedDict()

_NON_WORD_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_OPTION_LETTER_PREFIX = re.compile(r"^(?:[A-Da-d]|[1-4])[.)]\s+")

# Leading exam-forbidden framing — stripped in a loop from the start of stems/explanations.
_LEADING_META_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^(?:(?:according|based)\s+to\s+(?:the\s+)?(?:page|text|passage|source|excerpt|reading|document|book|textbook|material)"
        r"|from\s+(?:the\s+)?(?:page|text|passage|source|reading|document|book|textbook)"
        r"|in\s+(?:the\s+)?(?:passage|text|excerpt|reading|document|book|textbook|material)"
        r"|in\s+this\s+(?:book|text|reading|passage|document)"
        r"|on\s+page\s+\d+"
        r"|as\s+mentioned\s+(?:in|above|earlier|previously)"
        r"|refer\s+to\s+the\s+(?:figure|diagram|table|chart|image|example|text|passage|case)"
        r"|(?:the\s+)?(?:page|text|passage|source|reading|document|textbook)\s+(?:text\s+)?"
        r"(?:specifies|states|says|indicates|describes|explains|mentions)(?:\s+that)?)"
        r"[,:]?\s+",
        re.IGNORECASE,
    ),
    re.compile(r"^as\s+(?:the\s+)?(?:page|text|passage|reading|document)\s+(?:states|says)[,:]?\s+", re.IGNORECASE),
    re.compile(r"^the\s+text\s+states:\s*['\"]?", re.IGNORECASE),
    re.compile(r"^as\s+(?:stated|described|mentioned|shown|depicted|illustrated)\s+(?:in|above|earlier)[,:]?\s+", re.IGNORECASE),
    re.compile(r"^in\s+the\s+(?:given|provided)\s+(?:text|passage|reading|excerpt|example|case|scenario)[,:]?\s+", re.IGNORECASE),
    re.compile(r"^the\s+(?:above|aforementioned)[- ](?:mentioned\s+)?(?:text|passage|reading|example|case|scenario)[,:]?\s+", re.IGNORECASE),
)

# Exam-forbidden references anywhere in learner-visible MCQ text.
_DOCUMENT_META_RESIDUE_RE = re.compile(
    r"\b(?:"
    r"page\s+text|page\s+\d+|on\s+page\s+\d+|pages?\s+\d+\s*(?:and|–|-|—|to)\s*\d+"
    r"|first\s+page\s+of\s+the|last\s+page\s+of\s+the|this\s+page\s+of\s+the"
    r"|provided\s+content\s+of\s+the\s+(?:first\s+|last\s+)?page"
    r"|content\s+of\s+the\s+(?:first\s+|last\s+)?page"
    r"|(?:the\s+)?(?:pdf|document|file)\s+(?:page|itself|contains|contain|is\s+labeled)"
    r"|labeled\s+as\s+.{0,40}\b(?:pdf|document|page)\b"
    r"|zivo\s+test\s+pdf"
    r"|according\s+to\s+the\s+(?:given\s+|provided\s+)?(?:page|text|passage|reading|document|excerpt|source|book|textbook|material)"
    r"|based\s+on\s+the\s+(?:given\s+|provided\s+)?(?:page|text|passage|reading|document|excerpt|source|book|textbook|material)"
    r"|(?:the\s+)?(?:passage|reading|excerpt|document)\s+(?:on\s+page\s+\d+\s+)?(?:states|says|indicates|describes|explains|mentions)"
    r"|what\s+does\s+the\s+(?:passage|reading|excerpt|text|document|pdf)\s+(?:say|state|describe|mention|contain)"
    r"|what\s+is\s+the\s+provided\s+content"
    r"|from\s+the\s+(?:passage|reading|excerpt|text|document|source\s+material)"
    r"|in\s+this\s+(?:book|text|reading|passage|document|chapter|pdf)"
    r"|in\s+the\s+(?:passage|reading|excerpt|material|given\s+text|provided\s+text|pdf)"
    r"|(?:the\s+)?textbook\s+(?:says|states|describes|explains)"
    r"|(?:the\s+)?source\s+material"
    r"|as\s+(?:stated|described|mentioned|shown|depicted|illustrated)\s+in\s+the\s+(?:text|passage|reading|material|document|figure|diagram|table)"
    r"|the\s+text\s+(?:states|says|specifies)"
    r"|text\s+specifies|document\s+says|passage\s+states"
    r"|chapter\s+\d+\s+(?:states|says|describes|explains)"
    r"|as\s+(?:mentioned|noted)\s+(?:above|earlier|previously|in\s+the\s+(?:text|passage|reading|material|document))"
    r"|refer\s+to\s+the\s+(?:figure|diagram|table|chart|image|example|text|passage|case)"
    r"|the\s+(?:above|aforementioned|preceding|previous)[- ]?(?:mentioned\s+)?(?:text|passage|reading|example|case|scenario|discussion)"
    r")\b",
    re.IGNORECASE,
)

SUBJECT_MATTER_PREFIX = "Subject matter (internal reference — never mention in the question):"


def short_concept_label(
    raw: str | None, *, max_chars: int = CONCEPT_LABEL_MAX_CHARS
) -> str:
    """Clamp aspect/concept labels. Policy lives on KC Coverage."""
    return normalize_concept_label(raw, max_chars=max_chars)


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    def ratio() -> float:
        dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
        norm_a = sum(a * a for a in vec_a) ** 0.5
        norm_b = sum(b * b for b in vec_b) ** 0.5
        return pick(
            norm_a == 0.0 or norm_b == 0.0,
            lambda: 0.0,
            lambda: dot / (norm_a * norm_b),
        )

    return pick(
        not vec_a or not vec_b or len(vec_a) != len(vec_b),
        lambda: 0.0,
        ratio,
    )


def normalize_stem(text: str) -> str:
    lowered = (text or "").lower().strip()
    cleaned = _NON_WORD_RE.sub(" ", lowered)
    return " ".join(cleaned.split())


def aspect_signature(aspect: dict[str, Any]) -> str:
    label = str(aspect.get("label") or aspect.get("key") or "").strip()
    angle = str(aspect.get("cognitive_angle") or "").strip()
    return choose(bool(angle), f"{label} ({angle})", label)


def mcq_signature(mcq: dict[str, Any]) -> str:
    question = str(mcq.get("question") or mcq.get("stem") or "").strip()
    options = coerce_mcq_options(mcq.get("options") or mcq.get("choices"))
    try:
        ci = int(mcq.get("correct_index", 0))
    except (TypeError, ValueError):
        ci = 0
    correct = pick(
        0 <= ci < len(options),
        lambda: str(options[ci]).strip(),
        lambda: "",
    )
    return f"question: {question} | answer: {correct}"


def sanitize_mcq_option(text: str) -> str:
    """Strip leading A)/B. style prefixes — the UI renders letter labels."""
    return _OPTION_LETTER_PREFIX.sub("", (text or "").strip()).strip()


def _apply_leading_meta(text: str) -> str:
    current = text
    for pattern in _LEADING_META_PATTERNS:
        current = pattern.sub("", current).strip()
    return current


def _strip_leading_rounds(text: str, rounds_left: int) -> str:
    nxt = _apply_leading_meta(text)
    return pick(
        nxt == text or rounds_left <= 1,
        lambda: nxt,
        lambda: _strip_leading_rounds(nxt, rounds_left - 1),
    )


def strip_document_meta(text: str) -> str:
    """Remove leading exam-forbidden framing clauses."""
    # Collapse horizontal whitespace but PRESERVE newlines — statement-based,
    # match-the-following, ordering, and code stems carry meaningful line breaks
    # the UI renders (white-space: pre-line). Trim spaces around each newline and
    # cap blank-line runs so the layout stays tight.
    cleaned = re.sub(r"[^\S\n]+", " ", (text or "").strip())
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return pick(
        evaluate_presence(cleaned).action != "ok",
        lambda: "",
        lambda: re.sub(
            r"^(?:in\s+this\s+(?:book|text|reading|passage|document))[,:]?\s+",
            "",
            re.sub(
                r"^(?:on\s+page\s+\d+(?:\s+of\s+the\s+(?:text|book|passage))?)[,:]?\s+",
                "",
                re.sub(
                    r"\s*The text states:.*$",
                    "",
                    _strip_leading_rounds(cleaned, 8),
                    flags=re.IGNORECASE,
                ).strip(),
                flags=re.IGNORECASE,
            ).strip(),
            flags=re.IGNORECASE,
        ).strip(),
    )


def has_document_meta_residue(text: str) -> bool:
    return bool(_DOCUMENT_META_RESIDUE_RE.search(text or ""))


def has_document_meta_reference(text: str) -> bool:
    """True if learner-visible text still points at a book, page, passage, or document."""
    cleaned = (text or "").strip()
    return pick(
        evaluate_presence(cleaned).action != "ok",
        lambda: False,
        lambda: any(pattern.match(cleaned) for pattern in _LEADING_META_PATTERNS)
        or has_document_meta_residue(cleaned),
    )


def has_page_reference_stem(text: str) -> bool:
    """Backward-compatible alias — prefer has_document_meta_reference."""
    return has_document_meta_reference(text)


def _capitalize_first(text: str) -> str:
    return pick(
        bool(text) and text[0].islower(),
        lambda: text[0].upper() + text[1:],
        lambda: text,
    )


def sanitize_mcq_stem(text: str) -> str:
    """Remove exam-forbidden framing so the stem stands alone like a formal test item."""
    return _capitalize_first(strip_document_meta(text))


def sanitize_mcq_explanation(text: str) -> str:
    """Strip document/page/passage framing from stored explanations."""
    return _capitalize_first(strip_document_meta(text))


def sanitize_mcq_options(options: list[str]) -> list[str]:
    return list(map(_capitalize_first, filter(None, map(strip_document_meta, options))))


def coerce_mcq_options(raw: Any) -> list[str]:
    """Normalize MCQ options whether stored as a list or object-shaped JSON."""
    hit = first_match(
        _COERCE_RULES,
        {"is_list": isinstance(raw, list), "is_dict": isinstance(raw, dict)},
    )

    def from_list() -> list[str]:
        return list(filter(None, map(lambda o: sanitize_mcq_option(str(o)), raw)))

    def from_dict() -> list[str]:
        items = sorted(raw.items(), key=lambda kv: kv[0])
        return list(filter(None, map(lambda kv: sanitize_mcq_option(str(kv[1])), items)))

    return apply(hit.action, {"list": from_list, "dict": from_dict, "empty": lambda: []})


def prior_mcq_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    options = coerce_mcq_options(payload.get("options") or payload.get("choices"))
    try:
        ci = int(payload.get("correct_index", 0))
    except (TypeError, ValueError):
        ci = 0
    correct = pick(0 <= ci < len(options), lambda: str(options[ci]), lambda: "")
    return {
        "question": str(payload.get("question") or ""),
        "correct_answer": correct,
        "aspect_key": str(payload.get("primary_concept_key") or ""),
        "aspect_label": str(payload.get("primary_concept") or payload.get("primary_concept_key") or ""),
    }


def format_prior_mcqs_block(
    prior_mcqs: list[dict[str, Any]] | None, *, max_items: int | None = None
) -> str:
    def render() -> str:
        from app.services.quality_evaluation import plan_prior_mcq_prompt_items

        cap = plan_prior_mcq_prompt_items(max_items)
        recent = list(prior_mcqs[-cap:])
        lines = [
            "Recent questions already used — do NOT repeat these stems or test the same fact:"
        ]
        for i, item in enumerate(recent, start=1):
            label = item.get("aspect_label") or item.get("aspect_key") or "aspect"
            lines.append(f"{i}. [{label}] Q: {item.get('question', '')}")
        return "\n".join(lines) + "\n"

    return pick(not prior_mcqs, lambda: "", render)


def dedupe_aspects(
    aspects: list[dict[str, Any]],
    *,
    threshold: float = ASPECT_CLUSTER_THRESHOLD,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Compat wrapper — canonical seam is Aspect Discovery ``dedupe_aspects``."""
    from app.services.aspect_discovery import dedupe_aspects as engine_dedupe

    verdict = engine_dedupe(aspects, threshold=threshold)
    return list(verdict.aspects), {
        "raw_count": verdict.raw_count,
        "deduped_count": verdict.deduped_count,
        "merged_keys": list(verdict.merged_keys),
    }


def _evict_signature_cache() -> None:
    while len(_signature_embed_cache) > _SIGNATURE_EMBED_CACHE_MAX:
        _signature_embed_cache.popitem(last=False)


def embed_signature_cached(signature: str) -> list[float]:
    """Return embedding for an MCQ signature, reusing in-process cache."""
    cached = _signature_embed_cache.get(signature)

    def hit() -> list[float]:
        _signature_embed_cache.move_to_end(signature)
        return cached

    def miss() -> list[float]:
        vec = embed_texts([signature])[0]
        _signature_embed_cache[signature] = vec
        _signature_embed_cache.move_to_end(signature)
        _evict_signature_cache()
        return vec

    return pick(cached is not None, hit, miss)


def prior_mcq_embeddings(prior_mcqs: list[dict[str, Any]]) -> list[list[float]]:
    """Embed prior MCQ signatures once for repeated similarity checks."""
    def compute() -> list[list[float]]:
        prior_sigs = [
            mcq_signature(
                {
                    "question": p.get("question"),
                    "options": [p.get("correct_answer", "")],
                    "correct_index": 0,
                }
            )
            for p in prior_mcqs
        ]
        missing = list(filter(lambda s: s not in _signature_embed_cache, prior_sigs))

        def fill() -> None:
            for sig, vec in zip(missing, embed_texts(missing), strict=True):
                _signature_embed_cache[sig] = vec
                _signature_embed_cache.move_to_end(sig)
            _evict_signature_cache()

        pick(bool(missing), fill, lambda: None)
        return [embed_signature_cached(s) for s in prior_sigs]

    return pick(not prior_mcqs, lambda: [], compute)


def is_mcq_too_similar(
    mcq: dict[str, Any],
    prior_mcqs: list[dict[str, Any]] | None,
    *,
    threshold: float | None = None,
    prior_embeddings: list[list[float]] | None = None,
) -> tuple[bool, float]:
    """Compat wrapper — canonical seam is Quality ``judge_mcq_similarity``."""
    from app.services.quality_evaluation import (
        MCQ_SIMILARITY_THRESHOLD,
        judge_mcq_similarity,
    )

    verdict = judge_mcq_similarity(
        mcq,
        prior_mcqs,
        threshold=choose(threshold is None, MCQ_SIMILARITY_THRESHOLD, threshold),
        prior_embeddings=prior_embeddings,
    )
    return verdict.too_similar, verdict.max_similarity


def stems_match(a: str, b: str) -> bool:
    na = normalize_stem(a)
    nb = normalize_stem(b)
    return bool(na) and na == nb
