"""Anti-repeat gates — slug, topic fingerprint, embedding cosine."""

from __future__ import annotations

import hashlib
import re
import unicodedata

from sqlalchemy.orm import Session

from app.repositories import seo as seo_repo
from app.services.embed import embed_texts
from app.services.mcq_dedup import cosine_similarity
from app.services.seo_gate import NEAR_DUPE_COSINE, evaluate_embedding_near_dupe

_NON_WORD = re.compile(r"[^\w\s-]+", re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalize_topic(text: str) -> str:
    """Normalized topic key for fingerprint uniqueness."""
    lowered = unicodedata.normalize("NFKD", (text or "").lower())
    lowered = "".join(c for c in lowered if not unicodedata.combining(c))
    cleaned = _NON_WORD.sub(" ", lowered)
    cleaned = _SPACE.sub(" ", cleaned).strip()
    return cleaned[:200]


def topic_fingerprint(*parts: str) -> str:
    base = normalize_topic(" ".join(p for p in parts if p))
    if not base:
        return "empty"
    # Stable short hash suffix avoids collisions on truncated keys
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:10]
    slugish = base.replace(" ", "-")[:80]
    return f"{slugish}:{digest}"


def slugify(title: str, *, max_len: int = 80) -> str:
    base = normalize_topic(title).replace(" ", "-")
    base = re.sub(r"-+", "-", base).strip("-")
    if not base:
        base = "post"
    return base[:max_len]


def unique_slug(db: Session, title: str) -> str:
    base = slugify(title)
    candidate = base
    n = 2
    while seo_repo.slug_taken(db, candidate):
        suffix = f"-{n}"
        candidate = f"{base[: max(1, 80 - len(suffix))]}{suffix}"
        n += 1
        if n > 50:
            candidate = f"{base[:60]}-{hashlib.sha256(title.encode()).hexdigest()[:8]}"
            break
    return candidate


def embedding_near_dupe(db: Session, title: str, lede: str) -> tuple[bool, float]:
    """True if similar published post exists (cosine >= threshold)."""
    text = f"{title.strip()}\n{lede.strip()}".strip()
    if not text:
        return False, 0.0
    vectors = embed_texts([text])
    if not vectors or not vectors[0]:
        return False, 0.0
    sim = seo_repo.max_published_cosine(db, vectors[0])
    verdict = evaluate_embedding_near_dupe(sim)
    return verdict.is_dupe, verdict.similarity


def embed_title_lede(title: str, lede: str) -> list[float] | None:
    text = f"{title.strip()}\n{lede.strip()}".strip()
    if not text:
        return None
    vectors = embed_texts([text])
    if not vectors or not vectors[0]:
        return None
    return vectors[0]


def check_dedupe(
    db: Session,
    *,
    fingerprint: str,
    title: str,
    lede: str,
) -> tuple[bool, str]:
    """Return (ok, reason). ok=False means reject."""
    if seo_repo.fingerprint_taken(db, fingerprint):
        return False, "fingerprint_taken"
    near, sim = embedding_near_dupe(db, title, lede)
    if near:
        return False, f"embedding_near_dupe:{sim:.3f}"
    return True, "ok"


# Re-export for tests that want direct cosine without DB
__all__ = [
    "NEAR_DUPE_COSINE",
    "check_dedupe",
    "cosine_similarity",
    "embed_title_lede",
    "embedding_near_dupe",
    "normalize_topic",
    "slugify",
    "topic_fingerprint",
    "unique_slug",
]
