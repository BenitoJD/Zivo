"""Aspect Discovery Engine - which aspects to cook / seed on a page.

Design: docs/ENGINES.md (Aspect Discovery)
Version: qb.aspect_discovery.v1

Budget owns N; KC owns normalize. This engine owns discovery pick:
prefer central over support, keep enough for coverage, speculative
paragraph targets when triage has not landed yet, and next-unasked slice.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
from typing import Any, Literal, Sequence

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick

ASPECT_DISCOVERY_VERSION = "qb.aspect_discovery.v1"
DEFAULT_POLICY = "aspect_discovery_v1"

# Near-duplicate aspect cluster cutoff (embedding cosine).
ASPECT_CLUSTER_THRESHOLD = 0.88
# Failed cook attempts before marking an aspect asked/abandoned.
import os as _os

MAX_ASPECT_ATTEMPTS = int(_os.getenv("ZIVO_MAX_ASPECT_ATTEMPTS", "3"))
# Heuristic triage: ~one idea per N words; paragraphs shorter than this are noise.
FALLBACK_WORDS_PER_ASPECT = 120
FALLBACK_MIN_SUBSTANTIAL_WORDS = 12

Centrality = Literal["central", "support", "skip"]

_POLICY_ALIASES = ("default", "aspect", "discovery", "triage")
_CENTRALITY = {
    "central": "central",
    "support": "support",
    "skip": "skip",
    "peripheral": "support",
    "side": "support",
    "minor": "support",
    "secondary": "support",
}

_COOK_TARGET_RULES = (
    Rule(when=(Pred("has_unasked", "truthy"),), action="cook_unasked"),
    Rule(
        when=(Pred("spec_none", "truthy"), Pred("exhausted", "truthy")),
        action="close_coverage",
    ),
    Rule(when=(Pred("spec_none", "truthy"),), action="try_speculative"),
    Rule(when=(Pred("has_spec", "truthy"),), action="cook_spec"),
    Rule(when=(), action="none"),
)


def split_paragraphs(page_text: str | None) -> list[str]:
    return pick(
        not page_text,
        lambda: [],
        lambda: [p.strip() for p in filter(str.strip, page_text.split("\n\n"))],
    )


def substantial_paragraphs(
    page_text: str | None,
    *,
    min_words: int = FALLBACK_MIN_SUBSTANTIAL_WORDS,
) -> list[str]:
    """Paragraphs dense enough to count as a testable idea."""
    floor = max(1, int(min_words))
    return list(filter(lambda p: len(p.split()) >= floor, split_paragraphs(page_text)))


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


@dataclass(frozen=True)
class AspectAbandonVerdict:
    abandon: bool
    attempts: int
    max_attempts: int = MAX_ASPECT_ATTEMPTS
    policy: str = DEFAULT_POLICY
    policy_version: str = ASPECT_DISCOVERY_VERSION


def normalize_policy(policy: str | None) -> str:
    p = (policy or DEFAULT_POLICY).strip().lower()
    return choose(p in _POLICY_ALIASES, DEFAULT_POLICY, p or DEFAULT_POLICY)


def should_abandon_aspect(
    attempts: int,
    *,
    max_attempts: int = MAX_ASPECT_ATTEMPTS,
    policy: str | None = None,
) -> AspectAbandonVerdict:
    """After failed cooks, abandon (mark asked) so coverage can complete."""
    pol = normalize_policy(policy)
    n = max(0, int(attempts))
    cap = max(1, int(max_attempts))
    return AspectAbandonVerdict(
        abandon=n >= cap,
        attempts=n,
        max_attempts=cap,
        policy=pol,
    )


def heuristic_fallback_aspects(
    page_text: str,
    page_number: int,
    *,
    words_per_aspect: int = FALLBACK_WORDS_PER_ASPECT,
    min_substantial_words: int = FALLBACK_MIN_SUBSTANTIAL_WORDS,
    policy: str | None = None,
) -> AspectPickVerdict:
    """Density prior when LLM triage is off or returns empty aspects.

    ~one idea / ``words_per_aspect`` words; substantial paragraphs as labels.
    Budget still owns N via ``plan_page_budget`` after units are built.
    """
    from app.services.mcq_dedup import short_concept_label

    pol = normalize_policy(policy)
    words = len((page_text or "").split())
    paragraphs = split_paragraphs(page_text)
    substantial = substantial_paragraphs(page_text, min_words=min_substantial_words)
    word_estimate = words // max(1, words_per_aspect)
    word_estimate = choose(words > 0 and word_estimate == 0, 1, word_estimate)
    aspect_count = pick(
        bool(substantial),
        lambda: min(len(substantial), word_estimate),
        lambda: choose(words > 0, word_estimate, 0),
    )
    labels = choose(bool(substantial), substantial, paragraphs)
    aspects = [
        {
            "key": f"page-{page_number}-p{i + 1}",
            "label": short_concept_label(para.replace("\n", " "), max_chars=72),
            "centrality": "central",
            "asked": False,
            "answered": False,
        }
        for i, para in enumerate(labels[:aspect_count])
    ]
    aspects = pick(
        not aspects and words > 0,
        lambda: [
            {
                "key": f"page-{page_number}-main",
                "label": "Main ideas on this page",
                "centrality": "central",
                "asked": False,
                "answered": False,
            }
        ],
        lambda: aspects,
    )
    return AspectPickVerdict(
        aspects=tuple(aspects),
        n_requested=aspect_count,
        n_kept=len(aspects),
        policy=pol,
    )


def parse_centrality(raw: Any) -> Centrality:
    value = str(raw or "central").strip().lower()
    return _CENTRALITY.get(value, "central")  # type: ignore[return-value]


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

    def _cluster() -> AspectDedupeVerdict:
        signatures = [aspect_signature(a) for a in aspect_list]
        vectors = embed_texts(signatures)
        kept: list[dict[str, Any]] = []
        kept_vectors: list[list[float]] = []
        merged_keys: list[str] = []

        def _consider(aspect: dict[str, Any], vec: list[float]) -> None:
            is_dup = any(
                cosine_similarity(vec, kept_vec) >= threshold for kept_vec in kept_vectors
            )
            pick(
                is_dup,
                lambda: merged_keys.append(
                    str(aspect.get("key") or aspect.get("label") or "")
                ),
                lambda: (kept.append(aspect), kept_vectors.append(vec)),
            )

        for aspect, vec in zip(aspect_list, vectors, strict=True):
            _consider(aspect, vec)
        return AspectDedupeVerdict(
            aspects=tuple(kept),
            raw_count=raw_count,
            deduped_count=len(kept),
            merged_keys=tuple(merged_keys),
            threshold=threshold,
            policy=pol,
        )

    return pick(
        raw_count <= 1,
        lambda: AspectDedupeVerdict(
            aspects=tuple(aspect_list),
            raw_count=raw_count,
            deduped_count=raw_count,
            merged_keys=(),
            threshold=threshold,
            policy=pol,
        ),
        _cluster,
    )


def pick_for_plan(
    aspects: Sequence[dict[str, Any]],
    *,
    n_page: int,
    policy: str | None = None,
) -> AspectPickVerdict:
    """Prefer central units; keep enough aspects for coverage without exceeding plan."""
    pol = normalize_policy(policy)
    cookable = list(
        filter(lambda a: parse_centrality(a.get("centrality")) != "skip", aspects)
    )
    centrals = list(
        filter(lambda a: parse_centrality(a.get("centrality")) == "central", cookable)
    )
    supports = list(
        filter(lambda a: parse_centrality(a.get("centrality")) == "support", cookable)
    )
    ordered = centrals + supports
    keep = max(int(n_page), len(centrals))
    selected = pick(bool(keep), lambda: ordered[:keep], lambda: list(ordered))
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
    out = [
        dict(aspect)
        for aspect in islice(filter(lambda a: not a.get("asked"), aspects), n)
    ]
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

    def _from_text() -> AspectPickVerdict:
        paragraphs = [p.strip() for p in filter(str.strip, page_text.split("\n\n"))]
        targets = [
            {
                "key": f"page-{page_number}-spec-{i + 1}",
                "label": para[:120].replace("\n", " "),
                "asked": False,
                "answered": False,
                "speculative": True,
            }
            for i, para in enumerate(paragraphs[:n])
        ]
        targets = pick(
            bool(targets),
            lambda: targets,
            lambda: [
                {
                    "key": f"page-{page_number}-spec-main",
                    "label": "Main ideas on this page",
                    "asked": False,
                    "answered": False,
                    "speculative": True,
                }
            ],
        )
        return AspectPickVerdict(
            aspects=tuple(targets),
            n_requested=n,
            n_kept=len(targets),
            policy=pol,
        )

    return pick(
        not page_text,
        lambda: AspectPickVerdict(aspects=(), n_requested=n, n_kept=0, policy=pol),
        _from_text,
    )


CookTargetAction = Literal["cook", "close_coverage", "try_speculative", "none"]


@dataclass(frozen=True)
class CookTargetPlan:
    action: CookTargetAction
    targets: tuple[dict[str, Any], ...]
    reason: str
    policy: str = DEFAULT_POLICY
    policy_version: str = ASPECT_DISCOVERY_VERSION


def plan_cook_target_fallback(
    *,
    unasked: Sequence[dict[str, Any]],
    has_aspects: bool,
    speculative: Sequence[dict[str, Any]] | None = None,
) -> CookTargetPlan:
    """Unasked first; if none, close coverage or fall back to speculative."""
    from app.services.kc_coverage import evaluate_aspect_exhaustion_close

    hit = first_match(
        _COOK_TARGET_RULES,
        {
            "has_unasked": bool(unasked),
            "spec_none": speculative is None,
            "exhausted": evaluate_aspect_exhaustion_close(
                has_aspects=has_aspects, unasked_count=0
            ),
            "has_spec": bool(speculative),
        },
    )
    return apply(
        hit.action,
        {
            "cook_unasked": lambda: CookTargetPlan(
                "cook", tuple(dict(t) for t in unasked), "unasked"
            ),
            "close_coverage": lambda: CookTargetPlan("close_coverage", (), "exhausted"),
            "try_speculative": lambda: CookTargetPlan(
                "try_speculative", (), "need_speculative"
            ),
            "cook_spec": lambda: CookTargetPlan(
                "cook", tuple(dict(t) for t in speculative or ()), "speculative"
            ),
            "none": lambda: CookTargetPlan("none", (), "none"),
        },
    )


def plan_stalled_aspect_keys(
    targets: Sequence[dict[str, Any]],
    asked_keys: Sequence[str],
) -> tuple[str, ...]:
    """Targeted aspects that still have no saved question after this batch."""
    asked = {str(k) for k in asked_keys}
    keys = (str(target.get("key") or "") for target in targets)
    return tuple(filter(lambda key: key and key not in asked, keys))
