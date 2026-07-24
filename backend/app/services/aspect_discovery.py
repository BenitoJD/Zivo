"""Aspect Discovery Engine - which aspects to cook / seed on a page.

Design: docs/ENGINES.md (Aspect Discovery)
Version: qb.aspect_discovery.v1

Budget owns N; KC owns normalize. This engine owns discovery pick:
prefer central over support, keep enough for coverage, speculative
paragraph targets when triage has not landed yet, and next-unasked slice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence

ASPECT_DISCOVERY_VERSION = "qb.aspect_discovery.v1"
DEFAULT_POLICY = "aspect_discovery_v1"

# Near-duplicate aspect cluster cutoff (embedding cosine).
ASPECT_CLUSTER_THRESHOLD = 0.88

Centrality = Literal["central", "support", "skip"]


@dataclass(frozen=True)
class AspectPickVerdict:
    aspects: tuple[dict[str, Any], ...]
    n_requested: int
    n_kept: int
    policy: str = DEFAULT_POLICY
    policy_version: str = ASPECT_DISCOVERY_VERSION


@dataclass(frozen=True)
class AspectDedupeVerdict:
    aspects: tuple[dict[str, Any], ...]
    raw_count: int
    deduped_count: int
    merged_keys: tuple[str, ...]
    threshold: float = ASPECT_CLUSTER_THRESHOLD
    policy: str = DEFAULT_POLICY
    policy_version: str = ASPECT_DISCOVERY_VERSION


def normalize_policy(policy: str | None) -> str:
    p = (policy or DEFAULT_POLICY).strip().lower()
    if p in ("default", "aspect", "discovery", "triage"):
        return DEFAULT_POLICY
    return p or DEFAULT_POLICY


def parse_centrality(raw: Any) -> Centrality:
    value = str(raw or "central").strip().lower()
    if value in ("central", "support", "skip"):
        return value  # type: ignore[return-value]
    # LLM / legacy aliases — peripheral is support, never promote to central.
    if value in ("peripheral", "side", "minor", "secondary"):
        return "support"
    return "central"


def dedupe_aspects(
    aspects: Sequence[dict[str, Any]],
    *,
    threshold: float = ASPECT_CLUSTER_THRESHOLD,
    policy: str | None = None,
) -> AspectDedupeVerdict:
    """Cluster near-duplicate aspects by embedding cosine (owns threshold).

    Embedding / signature plumbing stays in ``mcq_dedup``.
    """
    from app.services.mcq_dedup import aspect_signature, cosine_similarity, embed_texts

    pol = normalize_policy(policy)
    aspect_list = list(aspects)
    raw_count = len(aspect_list)
    if raw_count <= 1:
        return AspectDedupeVerdict(
            aspects=tuple(aspect_list),
            raw_count=raw_count,
            deduped_count=raw_count,
            merged_keys=(),
            threshold=threshold,
            policy=pol,
        )

    signatures = [aspect_signature(a) for a in aspect_list]
    vectors = embed_texts(signatures)

    kept: list[dict[str, Any]] = []
    kept_vectors: list[list[float]] = []
    merged_keys: list[str] = []

    for aspect, vec in zip(aspect_list, vectors, strict=True):
        duplicate = False
        for kept_vec in kept_vectors:
            if cosine_similarity(vec, kept_vec) >= threshold:
                duplicate = True
                merged_keys.append(str(aspect.get("key") or aspect.get("label") or ""))
                break
        if not duplicate:
            kept.append(aspect)
            kept_vectors.append(vec)

    return AspectDedupeVerdict(
        aspects=tuple(kept),
        raw_count=raw_count,
        deduped_count=len(kept),
        merged_keys=tuple(merged_keys),
        threshold=threshold,
        policy=pol,
    )


def pick_for_plan(
    aspects: Sequence[dict[str, Any]],
    *,
    n_page: int,
    policy: str | None = None,
) -> AspectPickVerdict:
    """Prefer central units; keep enough aspects for coverage without exceeding plan."""
    pol = normalize_policy(policy)
    cookable = [a for a in aspects if parse_centrality(a.get("centrality")) != "skip"]
    centrals = [a for a in cookable if parse_centrality(a.get("centrality")) == "central"]
    supports = [a for a in cookable if parse_centrality(a.get("centrality")) == "support"]
    ordered = centrals + supports
    keep = max(int(n_page), len(centrals))
    selected = ordered[:keep] if keep else list(ordered)
    return AspectPickVerdict(
        aspects=tuple(selected),
        n_requested=int(n_page),
        n_kept=len(selected),
        policy=pol,
    )


def next_unasked(
    aspects: Sequence[dict[str, Any]],
    *,
    n: int,
    policy: str | None = None,
) -> AspectPickVerdict:
    """Next up-to-n aspects that have not been asked yet (coverage order)."""
    pol = normalize_policy(policy)
    n = max(0, int(n))
    out: list[dict[str, Any]] = []
    for aspect in aspects:
        if not aspect.get("asked"):
            out.append(dict(aspect))
            if len(out) >= n:
                break
    return AspectPickVerdict(
        aspects=tuple(out),
        n_requested=n,
        n_kept=len(out),
        policy=pol,
    )


def speculative_targets(
    page_text: str,
    page_number: int,
    n: int,
    *,
    policy: str | None = None,
) -> AspectPickVerdict:
    """Lightweight paragraph aspects when triage has not landed yet."""
    pol = normalize_policy(policy)
    n = max(1, int(n))
    if not page_text:
        return AspectPickVerdict(aspects=(), n_requested=n, n_kept=0, policy=pol)
    paragraphs = [p.strip() for p in page_text.split("\n\n") if p.strip()]
    targets: list[dict[str, Any]] = []
    for i, para in enumerate(paragraphs[:n]):
        label = para[:120].replace("\n", " ")
        targets.append(
            {
                "key": f"page-{page_number}-spec-{i + 1}",
                "label": label,
                "asked": False,
                "answered": False,
                "speculative": True,
            }
        )
    if not targets:
        targets.append(
            {
                "key": f"page-{page_number}-spec-main",
                "label": "Main ideas on this page",
                "asked": False,
                "answered": False,
                "speculative": True,
            }
        )
    return AspectPickVerdict(
        aspects=tuple(targets),
        n_requested=n,
        n_kept=len(targets),
        policy=pol,
    )
