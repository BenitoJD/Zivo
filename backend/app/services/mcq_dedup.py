"""Per-page MCQ dedup — aspect clustering, stem normalization, embedding similarity."""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any

from app.services.embed import embed_texts

ASPECT_CLUSTER_THRESHOLD = 0.88
MCQ_SIMILARITY_THRESHOLD = 0.92
_SIGNATURE_EMBED_CACHE_MAX = 2_048

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
        r"|(?:the\s+)?(?:page|text|passage|source|reading|document|textbook)\s+(?:text\s+)?"
        r"(?:specifies|states|says|indicates|describes|explains|mentions)(?:\s+that)?)"
        r"[,:]?\s+",
        re.IGNORECASE,
    ),
    re.compile(r"^as\s+(?:the\s+)?(?:page|text|passage|reading|document)\s+(?:states|says)[,:]?\s+", re.IGNORECASE),
    re.compile(r"^the\s+text\s+states:\s*['\"]?", re.IGNORECASE),
    re.compile(r"^as\s+(?:stated|described)\s+(?:in|above)[,:]?\s+", re.IGNORECASE),
)

# Exam-forbidden references anywhere in learner-visible MCQ text.
_DOCUMENT_META_RESIDUE_RE = re.compile(
    r"\b(?:"
    r"page\s+text|page\s+\d+|on\s+page\s+\d+|pages?\s+\d+\s*(?:and|–|-|—|to)\s*\d+"
    r"|according\s+to\s+the\s+(?:page|text|passage|reading|document|excerpt|source|book|textbook|material)"
    r"|based\s+on\s+the\s+(?:page|text|passage|reading|document|excerpt|source|book|textbook|material)"
    r"|(?:the\s+)?(?:passage|reading|excerpt|document)\s+(?:on\s+page\s+\d+\s+)?(?:states|says|indicates|describes|explains|mentions)"
    r"|what\s+does\s+the\s+(?:passage|reading|excerpt|text|document)\s+(?:say|state|describe|mention)"
    r"|from\s+the\s+(?:passage|reading|excerpt|text|document|source\s+material)"
    r"|in\s+this\s+(?:book|text|reading|passage|document|chapter)"
    r"|in\s+the\s+(?:passage|reading|excerpt|material)"
    r"|(?:the\s+)?textbook\s+(?:says|states|describes|explains)"
    r"|(?:the\s+)?source\s+material"
    r"|as\s+(?:stated|described)\s+in\s+the\s+(?:text|passage|reading|material|document)"
    r"|the\s+text\s+(?:states|says|specifies)"
    r"|text\s+specifies|document\s+says|passage\s+states"
    r"|chapter\s+\d+\s+(?:states|says|describes|explains)"
    r")\b",
    re.IGNORECASE,
)

SUBJECT_MATTER_PREFIX = "Subject matter (internal reference — never mention in the question):"


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def normalize_stem(text: str) -> str:
    lowered = (text or "").lower().strip()
    cleaned = _NON_WORD_RE.sub(" ", lowered)
    return " ".join(cleaned.split())


def aspect_signature(aspect: dict[str, Any]) -> str:
    label = str(aspect.get("label") or aspect.get("key") or "").strip()
    angle = str(aspect.get("cognitive_angle") or "").strip()
    if angle:
        return f"{label} ({angle})"
    return label


def mcq_signature(mcq: dict[str, Any]) -> str:
    question = str(mcq.get("question") or mcq.get("stem") or "").strip()
    options = coerce_mcq_options(mcq.get("options") or mcq.get("choices"))
    try:
        ci = int(mcq.get("correct_index", 0))
    except (TypeError, ValueError):
        ci = 0
    correct = ""
    if 0 <= ci < len(options):
        correct = str(options[ci]).strip()
    return f"question: {question} | answer: {correct}"


def sanitize_mcq_option(text: str) -> str:
    """Strip leading A)/B. style prefixes — the UI renders letter labels."""
    return _OPTION_LETTER_PREFIX.sub("", (text or "").strip()).strip()


def strip_document_meta(text: str) -> str:
    """Remove leading exam-forbidden framing clauses."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return ""
    for _ in range(8):
        changed = False
        for pattern in _LEADING_META_PATTERNS:
            updated = pattern.sub("", cleaned).strip()
            if updated != cleaned:
                cleaned = updated
                changed = True
        if not changed:
            break
    cleaned = re.sub(r"\s*The text states:.*$", "", cleaned, flags=re.IGNORECASE).strip()
    # Strip mid-stem page anchors when they appear as a leading clause before the real question.
    cleaned = re.sub(
        r"^(?:on\s+page\s+\d+(?:\s+of\s+the\s+(?:text|book|passage))?)[,:]?\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(
        r"^(?:in\s+this\s+(?:book|text|reading|passage|document))[,:]?\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    return cleaned


def has_document_meta_residue(text: str) -> bool:
    return bool(_DOCUMENT_META_RESIDUE_RE.search(text or ""))


def has_document_meta_reference(text: str) -> bool:
    """True if learner-visible text still points at a book, page, passage, or document."""
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    if any(pattern.match(cleaned) for pattern in _LEADING_META_PATTERNS):
        return True
    return has_document_meta_residue(cleaned)


def has_page_reference_stem(text: str) -> bool:
    """Backward-compatible alias — prefer has_document_meta_reference."""
    return has_document_meta_reference(text)


def _capitalize_first(text: str) -> str:
    if text and text[0].islower():
        return text[0].upper() + text[1:]
    return text


def sanitize_mcq_stem(text: str) -> str:
    """Remove exam-forbidden framing so the stem stands alone like a formal test item."""
    return _capitalize_first(strip_document_meta(text))


def sanitize_mcq_explanation(text: str) -> str:
    """Strip document/page/passage framing from stored explanations."""
    return _capitalize_first(strip_document_meta(text))


def sanitize_mcq_options(options: list[str]) -> list[str]:
    return [_capitalize_first(strip_document_meta(opt)) for opt in options if strip_document_meta(opt)]


def coerce_mcq_options(raw: Any) -> list[str]:
    """Normalize MCQ options whether stored as a list or object-shaped JSON."""
    if isinstance(raw, list):
        return [sanitize_mcq_option(str(o)) for o in raw if sanitize_mcq_option(str(o))]
    if isinstance(raw, dict):
        items = sorted(raw.items(), key=lambda kv: kv[0])
        return [sanitize_mcq_option(str(v)) for _, v in items if sanitize_mcq_option(str(v))]
    return []


def prior_mcq_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    options = coerce_mcq_options(payload.get("options") or payload.get("choices"))
    try:
        ci = int(payload.get("correct_index", 0))
    except (TypeError, ValueError):
        ci = 0
    correct = str(options[ci]) if 0 <= ci < len(options) else ""
    return {
        "question": str(payload.get("question") or ""),
        "correct_answer": correct,
        "aspect_key": str(payload.get("primary_concept_key") or ""),
        "aspect_label": str(payload.get("primary_concept") or payload.get("primary_concept_key") or ""),
    }


def format_prior_mcqs_block(prior_mcqs: list[dict[str, Any]] | None, *, max_items: int = 6) -> str:
    if not prior_mcqs:
        return ""
    recent = list(prior_mcqs[-max_items:])
    lines = [
        "Recent questions already used — do NOT repeat these stems or test the same fact:"
    ]
    for i, item in enumerate(recent, start=1):
        label = item.get("aspect_label") or item.get("aspect_key") or "aspect"
        lines.append(f"{i}. [{label}] Q: {item.get('question', '')}")
    return "\n".join(lines) + "\n"


def dedupe_aspects(
    aspects: list[dict[str, Any]],
    *,
    threshold: float = ASPECT_CLUSTER_THRESHOLD,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_count = len(aspects)
    if raw_count <= 1:
        return list(aspects), {
            "raw_count": raw_count,
            "deduped_count": raw_count,
            "merged_keys": [],
        }

    signatures = [aspect_signature(a) for a in aspects]
    vectors = embed_texts(signatures)

    kept: list[dict[str, Any]] = []
    kept_vectors: list[list[float]] = []
    merged_keys: list[str] = []

    for aspect, vec in zip(aspects, vectors, strict=True):
        duplicate = False
        for kept_vec in kept_vectors:
            if cosine_similarity(vec, kept_vec) >= threshold:
                duplicate = True
                merged_keys.append(str(aspect.get("key") or aspect.get("label") or ""))
                break
        if not duplicate:
            kept.append(aspect)
            kept_vectors.append(vec)

    return kept, {
        "raw_count": raw_count,
        "deduped_count": len(kept),
        "merged_keys": merged_keys,
    }


def embed_signature_cached(signature: str) -> list[float]:
    """Return embedding for an MCQ signature, reusing in-process cache."""
    cached = _signature_embed_cache.get(signature)
    if cached is not None:
        _signature_embed_cache.move_to_end(signature)
        return cached
    vec = embed_texts([signature])[0]
    _signature_embed_cache[signature] = vec
    _signature_embed_cache.move_to_end(signature)
    while len(_signature_embed_cache) > _SIGNATURE_EMBED_CACHE_MAX:
        _signature_embed_cache.popitem(last=False)
    return vec


def prior_mcq_embeddings(prior_mcqs: list[dict[str, Any]]) -> list[list[float]]:
    """Embed prior MCQ signatures once for repeated similarity checks."""
    if not prior_mcqs:
        return []
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
    missing = [s for s in prior_sigs if s not in _signature_embed_cache]
    if missing:
        for sig, vec in zip(missing, embed_texts(missing), strict=True):
            _signature_embed_cache[sig] = vec
            _signature_embed_cache.move_to_end(sig)
        while len(_signature_embed_cache) > _SIGNATURE_EMBED_CACHE_MAX:
            _signature_embed_cache.popitem(last=False)
    return [embed_signature_cached(s) for s in prior_sigs]


def is_mcq_too_similar(
    mcq: dict[str, Any],
    prior_mcqs: list[dict[str, Any]] | None,
    *,
    threshold: float = MCQ_SIMILARITY_THRESHOLD,
    prior_embeddings: list[list[float]] | None = None,
) -> tuple[bool, float]:
    """Return (too_similar, max_cosine) vs prior MCQs on the same page."""
    if not prior_mcqs and not prior_embeddings:
        return False, 0.0

    candidate_sig = mcq_signature(mcq)
    candidate_vec = embed_signature_cached(candidate_sig)
    if prior_embeddings is not None:
        prior_vecs = prior_embeddings
    else:
        prior_vecs = prior_mcq_embeddings(prior_mcqs or [])
    if not prior_vecs:
        return False, 0.0

    max_sim = 0.0
    for prior_vec in prior_vecs:
        sim = cosine_similarity(candidate_vec, prior_vec)
        max_sim = max(max_sim, sim)
    return max_sim >= threshold, max_sim


def stems_match(a: str, b: str) -> bool:
    na = normalize_stem(a)
    nb = normalize_stem(b)
    return bool(na) and na == nb
