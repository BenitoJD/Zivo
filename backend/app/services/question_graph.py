"""Question Graph / Lineage Engine — remediation and advance edges.

Design: docs/QUESTION_GRAPH_ENGINE.md
Version: qb.graph.v1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

GRAPH_VERSION = "qb.graph.v1"
LINK_FOLLOW_UP_AFTER_MISS = "follow_up_after_miss"
LINK_HARDER_THAN = "harder_than"
LINK_SAME_CONCEPT = "same_concept"


@dataclass(frozen=True)
class LineageEdge:
    from_id: str
    to_id: str
    kind: str


@dataclass(frozen=True)
class LineagePlan:
    edges: tuple[LineageEdge, ...]
    policy_version: str = GRAPH_VERSION


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
            edges.append(LineageEdge(a, b, LINK_FOLLOW_UP_AFTER_MISS))
            edges.append(LineageEdge(a, b, LINK_SAME_CONCEPT))
    for a, b in zip(ordered, ordered[1:]):
        edges.append(LineageEdge(a, b, LINK_HARDER_THAN))
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
