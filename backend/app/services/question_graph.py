"""Question Graph / Lineage Engine — remediation and advance edges.

Design: docs/QUESTION_GRAPH_ENGINE.md
Version: qb.graph.v1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence

GRAPH_VERSION = "qb.graph.v1"
DEFAULT_POLICY = "graph_v1"
LINK_FOLLOW_UP_AFTER_MISS = "follow_up_after_miss"
LINK_HARDER_THAN = "harder_than"
LINK_SAME_CONCEPT = "same_concept"

FOLLOW_UP_CONFIDENCE = 0.85
HARDER_THAN_CONFIDENCE = 0.7
SAME_CONCEPT_CONFIDENCE = 0.7

McqReuseScope = Literal["off", "demo", "all"]


@dataclass(frozen=True)
class LineageEdge:
    from_id: str
    to_id: str
    kind: str
    confidence: float = HARDER_THAN_CONFIDENCE


def lineage_confidence(kind: str) -> float:
    """Persist confidence for a planned edge kind."""
    if kind == LINK_FOLLOW_UP_AFTER_MISS:
        return FOLLOW_UP_CONFIDENCE
    if kind == LINK_SAME_CONCEPT:
        return SAME_CONCEPT_CONFIDENCE
    return HARDER_THAN_CONFIDENCE


@dataclass(frozen=True)
class LineagePlan:
    edges: tuple[LineageEdge, ...]
    policy: str = DEFAULT_POLICY
    policy_version: str = GRAPH_VERSION


@dataclass(frozen=True)
class McqReusePlan:
    """Whether cook may clone MCQs from identical page-hash bank items."""

    enabled: bool
    scope: McqReuseScope
    demo_only: bool
    policy: str = DEFAULT_POLICY
    policy_version: str = GRAPH_VERSION


def normalize_reuse_scope(scope: str | None) -> McqReuseScope:
    s = (scope or "all").strip().lower()
    if s in ("off", "demo", "all"):
        return s  # type: ignore[return-value]
    return "all"


def plan_mcq_reuse(scope: str | None = None) -> McqReusePlan:
    """Sole MCQ cross-document reuse seam (settings.mcq_reuse_scope).

    SQL fetch stays in generation_graph; this owns off / demo / all.
    """
    normalized = normalize_reuse_scope(scope)
    if normalized == "off":
        return McqReusePlan(enabled=False, scope="off", demo_only=False)
    if normalized == "demo":
        return McqReusePlan(enabled=True, scope="demo", demo_only=True)
    return McqReusePlan(enabled=True, scope="all", demo_only=False)


def plan_batch_lineage(finalized: Sequence[dict[str, Any]]) -> LineagePlan:
    """Plan edges for a cook batch: same-concept follow-ups + sequential harder_than.

    ``finalized`` items need ``_assertion_id`` and ``primary_concept_key``.
    """
    edges: list[LineageEdge] = []
    by_concept: dict[str, list[str]] = {}
    ordered: list[str] = []
    for item in finalized:
        aid = str(item.get("_assertion_id") or item.get("assertion_id") or "")
        if not aid:
            continue
        ordered.append(aid)
        ck = str(item.get("primary_concept_key") or "").strip()
        if ck:
            by_concept.setdefault(ck, []).append(aid)
    for ids in by_concept.values():
        for a, b in zip(ids, ids[1:]):
            edges.append(
                LineageEdge(
                    a, b, LINK_FOLLOW_UP_AFTER_MISS, lineage_confidence(LINK_FOLLOW_UP_AFTER_MISS)
                )
            )
            edges.append(
                LineageEdge(a, b, LINK_SAME_CONCEPT, lineage_confidence(LINK_SAME_CONCEPT))
            )
    for a, b in zip(ordered, ordered[1:]):
        edges.append(LineageEdge(a, b, LINK_HARDER_THAN, lineage_confidence(LINK_HARDER_THAN)))
    # Dedup
    seen: set[tuple[str, str, str]] = set()
    uniq: list[LineageEdge] = []
    for e in edges:
        key = (e.from_id, e.to_id, e.kind)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(e)
    return LineagePlan(edges=tuple(uniq))
