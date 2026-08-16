"""Anti-repeat gates — slug, topic fingerprint, embedding cosine."""

from __future__ import annotations

import hashlib
import re
import unicodedata

from sqlalchemy.orm import Session

from app.engine_runtime import pick
from app.repositories import seo as seo_repo
from app.services.embed import embed_texts
from app.services.mcq_dedup import cosine_similarity
from app.services.seo_gate import NEAR_DUPE_COSINE, evaluate_embedding_near_dupe

_NON_WORD = re.compile(r"[^\w\s-]+", re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalize_topic(text: str) -> str:
    """Normalized topic key for fingerprint uniqueness."""
    lowered = unicodedata.normalize("NFKD", (text or "").lower())
    lowered = "".join(filter(lambda c: not unicodedata.combining(c), lowered))
    cleaned = _NON_WORD.sub(" ", lowered)
    cleaned = _SPACE.sub(" ", cleaned).strip()
    return cleaned[:200]


def topic_fingerprint(*parts: str) -> str:
    base = normalize_topic(" ".join(filter(None, parts)))
    return pick(
        not base,
        lambda: "empty",
        lambda: f"{base.replace(' ', '-')[:80]}:{hashlib.sha256(base.encode('utf-8')).hexdigest()[:10]}",
    )


def slugify(title: str, *, max_len: int = 80) -> str:
    base = normalize_topic(title).replace(" ", "-")
    base = re.sub(r"-+", "-", base).strip("-") or "post"
    return base[:max_len]


def unique_slug(db: Session, title: str) -> str:
    base = slugify(title)
    candidate = base
    n = 2
    while seo_repo.slug_taken(db, candidate) and n <= 50:
        suffix = f"-{n}"
        candidate = f"{base[: max(1, 80 - len(suffix))]}{suffix}"
        n += 1
    return pick(
        seo_repo.slug_taken(db, candidate),
        lambda: f"{base[:60]}-{hashlib.sha256(title.encode()).hexdigest()[:8]}",
        lambda: candidate,
    )


def embedding_near_dupe(db: Session, title: str, lede: str) -> tuple[bool, float]:
    """True if similar published post exists (cosine >= threshold)."""
    text = f"{title.strip()}\n{lede.strip()}".strip()

    def _score() -> tuple[bool, float]:
        vectors = embed_texts([text])
        return pick(
            not vectors or not vectors[0],
            lambda: (False, 0.0),
            lambda: _from_vec(vectors[0]),
        )

    def _from_vec(vec: list[float]) -> tuple[bool, float]:
        verdict = evaluate_embedding_near_dupe(seo_repo.max_published_cosine(db, vec))
        return verdict.is_dupe, verdict.similarity

    return pick(not text, lambda: (False, 0.0), _score)


def embed_title_lede(title: str, lede: str) -> list[float] | None:
    text = f"{title.strip()}\n{lede.strip()}".strip()

    def _embed() -> list[float] | None:
        vectors = embed_texts([text])
        return pick(not vectors or not vectors[0], lambda: None, lambda: vectors[0])

    return pick(not text, lambda: None, _embed)


def check_dedupe(
    db: Session,
    *,
    fingerprint: str,
    title: str,
    lede: str,
) -> tuple[bool, str]:
    """Return (ok, reason). ok=False means reject."""
    near, sim = embedding_near_dupe(db, title, lede)
    return pick(
        seo_repo.fingerprint_taken(db, fingerprint),
        lambda: (False, "fingerprint_taken"),
        lambda: pick(near, lambda: (False, f"embedding_near_dupe:{sim:.3f}"), lambda: (True, "ok")),
    )


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
