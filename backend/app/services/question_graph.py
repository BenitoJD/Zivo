"""Question Graph / Lineage Engine — remediation and advance edges.

Design: docs/QUESTION_GRAPH_ENGINE.md
Version: qb.graph.v1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick

GRAPH_VERSION = "qb.graph.v1"
DEFAULT_POLICY = "graph_v1"
LINK_FOLLOW_UP_AFTER_MISS = "follow_up_after_miss"
LINK_HARDER_THAN = "harder_than"
LINK_SAME_CONCEPT = "same_concept"

FOLLOW_UP_CONFIDENCE = 0.85
HARDER_THAN_CONFIDENCE = 0.7
SAME_CONCEPT_CONFIDENCE = 0.7

McqReuseScope = Literal["off", "demo", "all"]

_CONFIDENCE = {
    LINK_FOLLOW_UP_AFTER_MISS: FOLLOW_UP_CONFIDENCE,
    LINK_SAME_CONCEPT: SAME_CONCEPT_CONFIDENCE,
}

_REUSE_RULES = (
    Rule(when=(Pred("scope", "eq", "off"),), action="off"),
    Rule(when=(Pred("scope", "eq", "demo"),), action="demo"),
    Rule(when=(), action="all"),
)


@dataclass(frozen=True)
class LineageEdge:
    from_id: str
    to_id: str
    kind: str
    confidence: float = HARDER_THAN_CONFIDENCE


def lineage_confidence(kind: str) -> float:
    """Persist confidence for a planned edge kind."""
    return _CONFIDENCE.get(kind, HARDER_THAN_CONFIDENCE)


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
    return choose(s in ("off", "demo", "all"), s, "all")  # type: ignore[return-value]


def plan_mcq_reuse(scope: str | None = None) -> McqReusePlan:
    """Sole MCQ cross-document reuse seam (settings.mcq_reuse_scope).

    SQL fetch stays in generation_graph; this owns off / demo / all.
    """
    normalized = normalize_reuse_scope(scope)
    hit = first_match(_REUSE_RULES, {"scope": normalized})
    return apply(
        hit.action,
        {
            "off": lambda: McqReusePlan(enabled=False, scope="off", demo_only=False),
            "demo": lambda: McqReusePlan(enabled=True, scope="demo", demo_only=True),
            "all": lambda: McqReusePlan(enabled=True, scope="all", demo_only=False),
        },
    )


def _templates_from_first_page(payloads: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    src_page = (payloads[0].get("artifact_id"), payloads[0].get("page_number"))
    same_page = filter(
        lambda payload: (payload.get("artifact_id"), payload.get("page_number")) == src_page,
        payloads,
    )
    usable = filter(
        lambda payload: payload.get("question") and payload.get("options"),
        same_page,
    )
    return tuple(dict(payload) for payload in usable)


def filter_reusable_templates(
    payloads: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Clone from the first source page only; later hash twins are near-dupes."""
    rows = list(payloads)
    return pick(not rows, lambda: (), lambda: _templates_from_first_page(rows))


def plan_batch_lineage(finalized: Sequence[dict[str, Any]]) -> LineagePlan:
    """Plan edges for a cook batch: same-concept follow-ups + sequential harder_than.

    ``finalized`` items need ``_assertion_id`` and ``primary_concept_key``.
    """
    rows = [
        (
            str(item.get("_assertion_id") or item.get("assertion_id") or ""),
            str(item.get("primary_concept_key") or "").strip(),
        )
        for item in finalized
    ]
    valid = list(filter(lambda row: row[0], rows))
    ordered = [aid for aid, _ck in valid]
    by_concept: dict[str, list[str]] = {}
    for aid, ck in filter(lambda row: row[1], valid):
        by_concept.setdefault(ck, []).append(aid)
    edges: list[LineageEdge] = []
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
    seen: set[tuple[str, str, str]] = set()

    def _keep(edge: LineageEdge) -> bool:
        key = (edge.from_id, edge.to_id, edge.kind)
        return pick(key in seen, lambda: False, lambda: seen.add(key) or True)

    uniq = list(filter(_keep, edges))
    return LineagePlan(edges=tuple(uniq))
