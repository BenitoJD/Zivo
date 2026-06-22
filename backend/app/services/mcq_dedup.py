"""Per-page MCQ dedup — aspect clustering, stem normalization, embedding similarity."""

from __future__ import annotations

import re
from typing import Any

from app.services.embed import embed_texts

ASPECT_CLUSTER_THRESHOLD = 0.88
MCQ_SIMILARITY_THRESHOLD = 0.92

_NON_WORD_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_OPTION_LETTER_PREFIX = re.compile(r"^(?:[A-Da-d]|[1-4])[.)]\s+")
_PAGE_REFERENCE_STEM = re.compile(
    r"^(?:(?:according|based)\s+to\s+(?:the\s+)?(?:page|text|passage|source|excerpt)"
    r"|from\s+(?:the\s+)?(?:page|text|passage|source)"
    r"|in\s+(?:the\s+)?(?:passage|text|excerpt)"
    r"|(?:the\s+)?(?:page|text|passage|source)\s+(?:states|says|indicates|describes|explains)(?:\s+that)?)"
    r"[,:]?\s+",
    re.IGNORECASE,
)


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


def sanitize_mcq_stem(text: str) -> str:
    """Remove meta framing like 'According to the page,' so the stem stands alone."""
    cleaned = (text or "").strip()
    while True:
        match = _PAGE_REFERENCE_STEM.match(cleaned)
        if not match:
            break
        cleaned = cleaned[match.end() :].strip()
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def has_page_reference_stem(text: str) -> bool:
    return bool(_PAGE_REFERENCE_STEM.match((text or "").strip()))


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


def format_prior_mcqs_block(prior_mcqs: list[dict[str, Any]] | None, *, max_items: int = 20) -> str:
    if not prior_mcqs:
        return ""
    lines = [
        "Prior questions on this page — do NOT repeat these facts, correct answers, or paraphrase these stems:"
    ]
    for i, item in enumerate(prior_mcqs[:max_items], start=1):
        label = item.get("aspect_label") or item.get("aspect_key") or "aspect"
        lines.append(
            f"{i}. [{label}] Q: {item.get('question', '')} | Correct: {item.get('correct_answer', '')}"
        )
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


def is_mcq_too_similar(
    mcq: dict[str, Any],
    prior_mcqs: list[dict[str, Any]] | None,
    *,
    threshold: float = MCQ_SIMILARITY_THRESHOLD,
) -> tuple[bool, float]:
    """Return (too_similar, max_cosine) vs prior MCQs on the same page."""
    if not prior_mcqs:
        return False, 0.0

    candidate_sig = mcq_signature(mcq)
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
    vectors = embed_texts([candidate_sig, *prior_sigs])
    candidate_vec = vectors[0]
    max_sim = 0.0
    for prior_vec in vectors[1:]:
        sim = cosine_similarity(candidate_vec, prior_vec)
        max_sim = max(max_sim, sim)
    return max_sim >= threshold, max_sim


def stems_match(a: str, b: str) -> bool:
    na = normalize_stem(a)
    nb = normalize_stem(b)
    return bool(na) and na == nb
