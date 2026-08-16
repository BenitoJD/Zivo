"""KC / Coverage Label Engine — aspect normalize + coverage completeness.

Design: docs/KC_COVERAGE_ENGINE.md
Version: qb.kc.v1
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

KC_VERSION = "qb.kc.v1"
DEFAULT_KC_POLICY = "aspect_v1"

_SLUG = re.compile(r"[^a-z0-9]+")


def normalize_key(raw: str | None) -> str:
    s = _SLUG.sub("-", (raw or "").strip().lower()).strip("-")
    return s or "unknown"


@dataclass(frozen=True)
class Aspect:
    key: str
    label: str
    central: bool = True


@dataclass(frozen=True)
class AspectPlan:
    aspects: tuple[Aspect, ...]
    non_content: bool = False
    policy_version: str = KC_VERSION


@dataclass(frozen=True)
class CoverageState:
    covered_keys: tuple[str, ...]
    uncovered_central: tuple[str, ...]
    complete: bool
    policy_version: str = KC_VERSION


def normalize_aspects(
    raw_aspects: Sequence[Mapping[str, Any]] | None,
    *,
    non_content: bool = False,
) -> AspectPlan:
    if non_content:
        return AspectPlan(aspects=(), non_content=True)
    out: list[Aspect] = []
    seen: set[str] = set()
    for a in raw_aspects or []:
        key = normalize_key(str(a.get("key") or a.get("label") or ""))
        if key in seen:
            continue
        seen.add(key)
        label = str(a.get("label") or key)
        central = bool(a.get("central", True))
        if a.get("peripheral") is True:
            central = False
        out.append(Aspect(key=key, label=label, central=central))
    return AspectPlan(aspects=tuple(out), non_content=False)


def coverage_state(
    plan: AspectPlan,
    covered_keys: Sequence[str] | None,
) -> CoverageState:
    covered = {normalize_key(k) for k in (covered_keys or []) if k}
    uncovered = tuple(
        a.key for a in plan.aspects if a.central and a.key not in covered
    )
    complete = plan.non_content or (bool(plan.aspects) and not uncovered) or (
        not plan.aspects and not plan.non_content
    )
    # Empty aspect list on content page → not complete (still cooking/unknown).
    if not plan.non_content and not plan.aspects:
        complete = False
    return CoverageState(
        covered_keys=tuple(sorted(covered)),
        uncovered_central=uncovered,
        complete=complete,
    )


def is_page_covered(entry: Mapping[str, Any] | None) -> bool:
    """Read a page_coverage entry and decide completeness via the engine."""
    entry = entry or {}
    if entry.get("non_content"):
        return True
    aspects = entry.get("aspects") or []
    plan = normalize_aspects(aspects, non_content=False)
    covered = [
        str(a.get("key"))
        for a in aspects
        if a.get("asked") or a.get("answered") or a.get("covered")
    ]
    return coverage_state(plan, covered).complete


@dataclass(frozen=True)
class PageCoverageVerdict:
    complete: bool
    reason: str
    policy_version: str = KC_VERSION


def evaluate_page_coverage_complete(
    entry: Mapping[str, Any] | None,
) -> PageCoverageVerdict:
    """Serve-path completeness: non-content, persist flag, or aspect coverage."""
    entry = entry or {}
    if entry.get("non_content"):
        return PageCoverageVerdict(True, "non_content")
    aspects = entry.get("aspects") or []
    if not aspects:
        return PageCoverageVerdict(False, "no_aspects")
    if entry.get("coverage_complete"):
        return PageCoverageVerdict(True, "flag")
    if is_page_covered(entry):
        return PageCoverageVerdict(True, "aspects_covered")
    return PageCoverageVerdict(False, "uncovered")


def should_persist_coverage_complete(entry: Mapping[str, Any] | None) -> bool:
    """Do not stamp complete on a page that never landed triage aspects."""
    return bool((entry or {}).get("aspects"))


def mark_aspects_answered(
    aspects: Sequence[Mapping[str, Any]],
    concept_key: str,
) -> list[dict[str, Any]]:
    """Mark the matching aspect answered after a grade."""
    key = str(concept_key or "")
    out: list[dict[str, Any]] = []
    for aspect in aspects:
        row = dict(aspect)
        if row.get("key") == key:
            row["answered"] = True
        out.append(row)
    return out


def evaluate_aspect_exhaustion_close(
    *,
    has_aspects: bool,
    unasked_count: int,
) -> bool:
    """Close coverage when triage landed and nothing remains unasked."""
    return bool(has_aspects) and int(unasked_count) <= 0
