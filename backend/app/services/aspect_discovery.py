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

Centrality = Literal["central", "support", "skip"]


@dataclass(frozen=True)
class AspectPickVerdict:
    aspects: tuple[dict[str, Any], ...]
    n_requested: int
    n_kept: int
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
    return "central"


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
