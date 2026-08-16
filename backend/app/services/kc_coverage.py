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


def normalize_key(raw: str | None) -> str:
    s = _SLUG.sub("-", (raw or "").strip().lower()).strip("-")
    return s or "unknown"


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
    if not s:
        return CONCEPT_LABEL_FALLBACK
    for sep in (". ", "? ", "! ", "; ", " — ", " – ", " - "):
        if sep in s:
            s = s.split(sep, 1)[0].strip()
            break
    # Mid-word residue from newspaper/OCR extracts ("ngress government…").
    if s[:1].islower():
        parts = s.split()
        for i, w in enumerate(parts):
            if w[:1].isupper():
                s = " ".join(parts[i:])
                break
        else:
            return CONCEPT_LABEL_FALLBACK
    # Sentence-as-aspect → keep the subject ("Osmosis is the net…" → "Osmosis").
    copula = _CONCEPT_COPULA.search(s)
    if copula and copula.start() > 0:
        subject = s[: copula.start()].strip(" ,;:-")
        if 1 <= len(subject.split()) <= CONCEPT_LABEL_MAX_WORDS:
            s = subject
    words = s.split()
    if len(words) > CONCEPT_LABEL_MAX_WORDS:
        s = " ".join(words[:CONCEPT_LABEL_MAX_WORDS])
        words = s.split()
    # Drop dangling clause openers left by mid-sentence truncation
    # ("… in plants that" → "… in plants").
    while words and words[-1].lower() in _DANGLING_LABEL_WORDS:
        words.pop()
        s = " ".join(words)
    if len(s) > max_chars:
        s = s[: max(1, max_chars - 1)].rstrip(" ,;:-") + "…"
    return s or CONCEPT_LABEL_FALLBACK


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


def stamp_kc_centrality(*, orig_centrality: str, central: bool) -> tuple[str, bool]:
    """Keep skip for budget weights; otherwise KC central bool maps to central/support."""
    if orig_centrality == "skip":
        return "skip", False
    return ("central" if central else "support"), bool(central)


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
    if not entry.get("coverage_complete"):
        return False
    if entry.get("aspects"):
        return False
    return int(mcq_count) <= 0


def coverage_aspects_field(serve_mode: str) -> str:
    """Learn cooks `aspects`; Test cooks the dual-mode `test_aspects` overlay."""
    return "test_aspects" if serve_mode == "test" else "aspects"
