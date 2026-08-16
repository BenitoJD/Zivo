"""KC / Coverage Label Engine — aspect normalize + coverage completeness.

Design: docs/KC_COVERAGE_ENGINE.md
Version: qb.kc.v1
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.engine_runtime import Pred, Rule, choose, first_match, pick

KC_VERSION = "qb.kc.v1"
DEFAULT_KC_POLICY = "aspect_v1"

CONCEPT_LABEL_MAX_CHARS = 56
CONCEPT_LABEL_MAX_WORDS = 8
CONCEPT_LABEL_FALLBACK = "General"

_SLUG = re.compile(r"[^a-z0-9]+")
_CONCEPT_COPULA = re.compile(
    r"\s+(?:is|are|was|were|means|refers|describes|involves)\s+",
    flags=re.IGNORECASE,
)
_DANGLING_LABEL_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "in",
        "on",
        "to",
        "for",
        "with",
        "from",
        "into",
        "onto",
        "across",
        "by",
        "via",
        "as",
        "at",
        "that",
        "which",
        "who",
        "whom",
        "whose",
        "where",
        "when",
        "is",
        "are",
        "was",
        "were",
    }
)
_LABEL_SEPS = (". ", "? ", "! ", "; ", " — ", " – ", " - ")

_PAGE_COV_RULES = (
    Rule(when=(Pred("non_content", "truthy"),), action="non_content", extras={"complete": True}),
    Rule(when=(Pred("no_aspects", "truthy"),), action="no_aspects", extras={"complete": False}),
    Rule(when=(Pred("flag", "truthy"),), action="flag", extras={"complete": True}),
    Rule(when=(Pred("covered", "truthy"),), action="aspects_covered", extras={"complete": True}),
    Rule(when=(), action="uncovered", extras={"complete": False}),
)


def normalize_key(raw: str | None) -> str:
    s = _SLUG.sub("-", (raw or "").strip().lower()).strip("-")
    return s or "unknown"


def _cut_label_clause(s: str) -> str:
    sep = next(filter(lambda sp: sp in s, _LABEL_SEPS), None)
    return pick(sep is None, lambda: s, lambda: s.split(sep, 1)[0].strip())


def _from_first_upper_word(s: str) -> str | None:
    parts = s.split()
    idx = next(filter(lambda i: parts[i][:1].isupper(), range(len(parts))), None)
    return pick(idx is None, lambda: None, lambda: " ".join(parts[idx:]))


def _repair_leading_lower(s: str) -> str:
    return pick(
        not s[:1].islower(),
        lambda: s,
        lambda: _from_first_upper_word(s) or CONCEPT_LABEL_FALLBACK,
    )


def _take_subject(s: str, copula: re.Match[str]) -> str:
    subject = s[: copula.start()].strip(" ,;:-")
    return choose(1 <= len(subject.split()) <= CONCEPT_LABEL_MAX_WORDS, subject, s)


def _clip_copula_subject(s: str) -> str:
    copula = _CONCEPT_COPULA.search(s)
    return pick(
        copula is not None and copula.start() > 0,
        lambda: _take_subject(s, copula),
        lambda: s,
    )


def _cap_label_words(s: str) -> str:
    words = s.split()
    return pick(
        len(words) > CONCEPT_LABEL_MAX_WORDS,
        lambda: " ".join(words[:CONCEPT_LABEL_MAX_WORDS]),
        lambda: s,
    )


def _drop_dangling_label_words(s: str) -> str:
    words = s.split()
    while words and words[-1].lower() in _DANGLING_LABEL_WORDS:
        words.pop()
    return " ".join(words)


def _cap_label_chars(s: str, max_chars: int) -> str:
    return pick(
        len(s) > max_chars,
        lambda: s[: max(1, max_chars - 1)].rstrip(" ,;:-") + "…",
        lambda: s,
    )


def normalize_concept_label(
    raw: str | None,
    *,
    max_chars: int = CONCEPT_LABEL_MAX_CHARS,
) -> str:
    """Clamp aspect/concept labels for report cards and bank metadata.

    Fallback triage sometimes uses a whole paragraph as the aspect label; that
    must not land as a topic chip. Prefer a noun-phrase subject before a
    copula, else the first clause, then word/char caps.
    """
    s = re.sub(r"\s+", " ", (raw or "").strip())
    return pick(
        not s,
        lambda: CONCEPT_LABEL_FALLBACK,
        lambda: (
            _cap_label_chars(
                _drop_dangling_label_words(
                    _cap_label_words(
                        _clip_copula_subject(_repair_leading_lower(_cut_label_clause(s)))
                    )
                ),
                max_chars,
            )
            or CONCEPT_LABEL_FALLBACK
        ),
    )


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


def _record_aspect(a: Mapping[str, Any], key: str, seen: set[str]) -> Aspect:
    seen.add(key)
    central = bool(a.get("central", True)) and a.get("peripheral") is not True
    return Aspect(key=key, label=str(a.get("label") or key), central=central)


def _one_aspect(a: Mapping[str, Any], seen: set[str]) -> Aspect | None:
    key = normalize_key(str(a.get("key") or a.get("label") or ""))
    return pick(
        key in seen,
        lambda: None,
        lambda: _record_aspect(a, key, seen),
    )


def _aspects_from_raw(raw_aspects: Sequence[Mapping[str, Any]] | None) -> AspectPlan:
    seen: set[str] = set()
    out: list[Aspect] = []
    for a in raw_aspects or []:
        out.extend(filter(None, (_one_aspect(a, seen),)))
    return AspectPlan(aspects=tuple(out), non_content=False)


def normalize_aspects(
    raw_aspects: Sequence[Mapping[str, Any]] | None,
    *,
    non_content: bool = False,
) -> AspectPlan:
    return pick(
        non_content,
        lambda: AspectPlan(aspects=(), non_content=True),
        lambda: _aspects_from_raw(raw_aspects),
    )


def coverage_state(
    plan: AspectPlan,
    covered_keys: Sequence[str] | None,
) -> CoverageState:
    covered = {normalize_key(k) for k in filter(None, covered_keys or [])}
    uncovered = tuple(
        a.key for a in filter(lambda a: a.central and a.key not in covered, plan.aspects)
    )
    complete = plan.non_content or (bool(plan.aspects) and not uncovered)
    return CoverageState(
        covered_keys=tuple(sorted(covered)),
        uncovered_central=uncovered,
        complete=complete,
    )


def is_page_covered(entry: Mapping[str, Any] | None) -> bool:
    """Read a page_coverage entry and decide completeness via the engine."""
    entry = entry or {}
    aspects = entry.get("aspects") or []
    return pick(
        bool(entry.get("non_content")),
        lambda: True,
        lambda: coverage_state(
            normalize_aspects(aspects, non_content=False),
            [
                str(a.get("key"))
                for a in filter(
                    lambda a: a.get("asked") or a.get("answered") or a.get("covered"),
                    aspects,
                )
            ],
        ).complete,
    )


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
    aspects = entry.get("aspects") or []
    hit = first_match(
        _PAGE_COV_RULES,
        {
            "non_content": bool(entry.get("non_content")),
            "no_aspects": not aspects,
            "flag": bool(entry.get("coverage_complete")),
            "covered": is_page_covered(entry),
        },
    )
    return PageCoverageVerdict(bool(hit.extras["complete"]), hit.action)


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
        row.update(choose(row.get("key") == key, {"answered": True}, {}))
        out.append(row)
    return out


def evaluate_aspect_exhaustion_close(
    *,
    has_aspects: bool,
    unasked_count: int,
) -> bool:
    """Close coverage when triage landed and nothing remains unasked."""
    return bool(has_aspects) and int(unasked_count) <= 0


def stamp_kc_centrality(*, orig_centrality: str, central: bool) -> tuple[str, bool]:
    """Keep skip for budget weights; otherwise KC central bool maps to central/support."""
    return pick(
        orig_centrality == "skip",
        lambda: ("skip", False),
        lambda: (choose(central, "central", "support"), bool(central)),
    )


def stamp_aspect_flags(centrality: str) -> tuple[bool, bool]:
    """(central, peripheral) from a parsed centrality token."""
    cent = (centrality or "").strip().lower()
    return cent == "central", cent == "support"


def evaluate_stale_coverage_stamp(
    entry: Mapping[str, Any] | None,
    *,
    mcq_count: int,
) -> bool:
    """True when coverage_complete was stamped before triage aspects landed."""
    entry = entry or {}
    return (
        bool(entry.get("coverage_complete"))
        and not entry.get("aspects")
        and int(mcq_count) <= 0
    )


def coverage_aspects_field(serve_mode: str) -> str:
    """Learn cooks `aspects`; Test cooks the dual-mode `test_aspects` overlay."""
    return choose(serve_mode == "test", "test_aspects", "aspects")
