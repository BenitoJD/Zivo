"""Question budget planner: pure functions (policy seam).

Design: docs/QUESTION_BUDGET_ENGINE.md
Version: qb.budget.v1

Deterministic core only. LLM may propose units/centrality; this module owns N.
Pipeline batch sizes (REFILL_BATCH_SIZE) live elsewhere and must not enter here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Sequence

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick

BUDGET_VERSION = "qb.budget.v1"

Mode = Literal["learn", "test"]
Centrality = Literal["central", "support", "skip"]

WEIGHT: dict[Centrality, float] = {
    "central": 1.0,
    "support": 0.5,
    "skip": 0.0,
}

M_LEARN = 1
M_TEST_FORMATIVE = 3
M_TEST_HIGH_STAKES = 5

W_PER_UNIT = 120
SESSION_SOFT = 20
# Speculative N_page when triage has not landed yet (confidence=low seed).
SPECULATIVE_PAGE_N = 5
# Public practice: a Wikidata concept is ready once this many questions exist.
PRACTICE_CONCEPT_MIN_QUESTIONS = 5
# Coding bank: one verified problem per programmable page.
CODING_PROBLEMS_PER_PAGE = 1
# Public Wikipedia sources are split into this many chars per pseudo-page.
PRACTICE_SOURCE_CHUNK_CHARS = 2500

# Formative Test session information floor (IRT): SE=0.40 → I*=6.25
SE_TARGET_FORMATIVE = 0.40
I_BAR_DEFAULT = 0.20

_TEST_BUDGET_RULES = (
    Rule(when=(Pred("non_content", "truthy"),), action="zero"),
    Rule(when=(Pred("has_stored", "truthy"),), action="stored"),
    Rule(when=(Pred("has_units", "truthy"),), action="replan"),
    Rule(when=(), action="zero"),
)

_PAGE_BUDGET_RULES = (
    Rule(when=(Pred("non_content", "truthy"),), action="zero"),
    Rule(when=(Pred("newspaper_test", "truthy"),), action="newspaper"),
    Rule(when=(Pred("replan", "truthy"),), action="replan"),
    Rule(when=(Pred("stored_missing", "truthy"), Pred("omit", "truthy")), action="omit"),
    Rule(when=(Pred("stored_missing", "truthy"),), action="speculative"),
    Rule(when=(), action="stored"),
)


@dataclass(frozen=True)
class Unit:
    """One testable knowledge component on a page."""

    key: str
    centrality: Centrality = "central"


@dataclass(frozen=True)
class PageBudgetPlan:
    n_page: int
    n_cov: float
    mode: Mode
    confidence: Literal["high", "medium", "low"]
    budget_version: str = BUDGET_VERSION


@dataclass(frozen=True)
class DocumentBudgetPlan:
    n_doc: int
    n_doc_cov: int
    n_info: int
    mode: Mode
    budget_version: str = BUDGET_VERSION


def mode_multiplier(mode: Mode, *, high_stakes: bool = False) -> int:
    return choose(
        mode == "learn",
        M_LEARN,
        choose(high_stakes, M_TEST_HIGH_STAKES, M_TEST_FORMATIVE),
    )


def parse_budget_mode(value: str | None) -> Mode:
    """Normalize API / progress values. Anything other than test → learn."""
    return choose((value or "").strip().lower() == "test", "test", "learn")


def units_from_aspect_dicts(aspects: Sequence[dict[str, object]] | None) -> list[Unit]:
    """Build planner units from persisted page_coverage aspects."""

    def _as_unit(i: int, raw: object) -> Unit | None:
        def _from_dict() -> Unit:
            key = str(raw.get("key") or f"aspect-{i + 1}")
            cent_raw = str(raw.get("centrality") or "central").strip().lower()
            centrality: Centrality = choose(cent_raw in WEIGHT, cent_raw, "central")
            return Unit(key=key, centrality=centrality)

        return pick(not isinstance(raw, dict), lambda: None, _from_dict)

    return list(
        filter(None, (_as_unit(i, raw) for i, raw in enumerate(aspects or [])))
    )


def coverage_score(units: Sequence[Unit], mode: Mode, *, high_stakes: bool = False) -> float:
    m = mode_multiplier(mode, high_stakes=high_stakes)
    return sum(WEIGHT[u.centrality] * m for u in units)


def density_unit_count(words: int, substantial_paragraphs: int) -> int:
    """Heuristic U_hat when triage aspects are missing (matches legacy ~1/120 words)."""
    by_words = max(1, words // W_PER_UNIT)
    return pick(
        words <= 0,
        lambda: 0,
        lambda: choose(
            substantial_paragraphs > 0,
            min(substantial_paragraphs, by_words),
            by_words,
        ),
    )


def information_floor_items(
    *,
    se_target: float = SE_TARGET_FORMATIVE,
    i_bar: float = I_BAR_DEFAULT,
) -> int:
    """N_info = ceil(I* / Ĩ) with I* = 1/SE². Session/document Test floor, not per-page."""

    def _bad() -> int:
        raise ValueError("se_target and i_bar must be positive")

    def _ok() -> int:
        i_star = 1.0 / (se_target * se_target)
        return int(math.ceil(i_star / i_bar))

    return pick(se_target <= 0 or i_bar <= 0, _bad, _ok)


def speculative_page_budget(*, mode: Mode = "learn") -> PageBudgetPlan:
    """Seed N_page before triage lands so generation can start (confidence=low)."""
    n = max(0, int(SPECULATIVE_PAGE_N))
    return PageBudgetPlan(n_page=n, n_cov=float(n), mode=mode, confidence="low")


def plan_page_budget(
    units: Sequence[Unit] | None,
    *,
    mode: Mode = "learn",
    non_content: bool = False,
    high_stakes: bool = False,
    words: int = 0,
    substantial_paragraphs: int = 0,
    ceil_page: int | None = None,
    confidence: Literal["high", "medium", "low"] | None = None,
) -> PageBudgetPlan:
    """Compute N_page from weighted units × mode evidence (or density prior)."""

    def _density() -> tuple[float, Literal["high", "medium", "low"]]:
        u_hat = density_unit_count(words, substantial_paragraphs)
        synthetic = [Unit(key=f"density-{i}", centrality="central") for i in range(u_hat)]
        n_cov = coverage_score(synthetic, mode, high_stakes=high_stakes)
        conf: Literal["high", "medium", "low"] = confidence or choose(
            bool(u_hat), "medium", "high"
        )
        return n_cov, conf

    def _from_units_or_density() -> PageBudgetPlan:
        n_cov, conf = pick(
            bool(units),
            lambda: (
                coverage_score(units, mode, high_stakes=high_stakes),
                confidence or "high",
            ),
            _density,
        )
        n_page = max(0, int(round(n_cov)))
        n_page = pick(ceil_page is not None, lambda: min(ceil_page, n_page), lambda: n_page)
        return PageBudgetPlan(n_page=n_page, n_cov=n_cov, mode=mode, confidence=conf)

    return pick(
        non_content,
        lambda: PageBudgetPlan(
            n_page=0,
            n_cov=0.0,
            mode=mode,
            confidence=confidence or "high",
        ),
        _from_units_or_density,
    )


def plan_document_budget(
    page_budgets: Iterable[int],
    *,
    mode: Mode = "learn",
    high_stakes: bool = False,
    se_target: float = SE_TARGET_FORMATIVE,
    i_bar: float = I_BAR_DEFAULT,
) -> DocumentBudgetPlan:
    """Sum page budgets; Test applies an information floor at document/session scope."""
    n_doc_cov = sum(max(0, int(n)) for n in page_budgets)
    se_used = choose(
        mode == "test" and high_stakes and se_target == SE_TARGET_FORMATIVE,
        0.30,
        se_target,
    )
    n_info = pick(
        mode == "test",
        lambda: information_floor_items(se_target=se_used, i_bar=i_bar),
        lambda: 0,
    )
    n_doc = choose(mode == "test", max(n_doc_cov, n_info), n_doc_cov)
    return DocumentBudgetPlan(
        n_doc=n_doc,
        n_doc_cov=n_doc_cov,
        n_info=n_info,
        mode=mode,
    )


def exceeds_page_budget(sequence: int, budget: int) -> bool:
    """True when the next sequence slot is past this page's N."""
    return int(sequence) > max(0, int(budget))


def evaluate_generation_stop(
    *,
    generated: int,
    budget: int,
    coverage_complete: bool,
) -> bool:
    """Stop refill when coverage is done or generated items already meet N."""
    return coverage_complete or int(generated) >= max(0, int(budget))


def resolve_test_question_budget(
    *,
    non_content: bool,
    stored_test_budget: int | None,
    units: Sequence[Unit] | None,
    confidence: Literal["high", "medium", "low"] | None,
) -> int:
    """Newspaper / Test N_page: stored cook target, else replan from aspects."""
    hit = first_match(
        _TEST_BUDGET_RULES,
        {
            "non_content": non_content,
            "has_stored": stored_test_budget is not None,
            "has_units": bool(units),
        },
    )
    return apply(
        hit.action,
        {
            "zero": lambda: 0,
            "stored": lambda: max(0, int(stored_test_budget)),
            "replan": lambda: plan_page_budget(
                units, mode="test", confidence=confidence
            ).n_page,
        },
    )


def resolve_page_budget(
    *,
    non_content: bool,
    newspaper_test: bool,
    newspaper_test_n: int,
    serve_mode: Mode,
    persisted_mode: Mode,
    units: Sequence[Unit] | None,
    confidence: Literal["high", "medium", "low"] | None,
    stored_budget: int | None,
    missing: Literal["speculative", "omit"] = "speculative",
) -> int | None:
    """Stored vs replanned N_page for the active serve mode.

    ``missing='omit'`` skips untriaged pages in a document rollup; serve uses
    ``'speculative'`` so a page without stored N still has a seed budget.
    """
    hit = first_match(
        _PAGE_BUDGET_RULES,
        {
            "non_content": non_content,
            "newspaper_test": newspaper_test,
            "replan": bool(units) and serve_mode != persisted_mode,
            "stored_missing": stored_budget is None,
            "omit": missing == "omit",
        },
    )
    return apply(
        hit.action,
        {
            "zero": lambda: 0,
            "newspaper": lambda: max(0, int(newspaper_test_n)),
            "replan": lambda: plan_page_budget(
                units, mode=serve_mode, confidence=confidence
            ).n_page,
            "omit": lambda: None,
            "speculative": lambda: speculative_page_budget(mode=serve_mode).n_page,
            "stored": lambda: max(0, int(stored_budget)),
        },
    )


@dataclass(frozen=True)
class NewspaperTestTriagePlan:
    apply: bool
    content_type: str | None
    test_question_budget: int
    test_aspects: tuple[dict[str, Any], ...]
    budget_version: str = BUDGET_VERSION


def plan_newspaper_test_triage(
    *,
    is_newspaper: bool,
    non_content: bool,
    aspects: Sequence[dict[str, Any]],
    units: Sequence[Unit] | None,
    words: int,
    substantial_paragraphs: int,
    confidence: Literal["high", "medium", "low"] | None,
) -> NewspaperTestTriagePlan:
    """Newspaper pages also persist a Test overlay (N + aspects) at Learn triage."""

    def _overlay() -> NewspaperTestTriagePlan:
        from app.services.aspect_discovery import pick_for_plan
        from app.services.newspaper_ad_filter import NEWSPAPER_EXAM_CONTENT_TYPE

        test_plan = plan_page_budget(
            units or None,
            mode="test",
            non_content=False,
            words=words,
            substantial_paragraphs=substantial_paragraphs,
            confidence=confidence,
        )
        picked = pick_for_plan(list(aspects), n_page=test_plan.n_page)
        return NewspaperTestTriagePlan(
            True,
            NEWSPAPER_EXAM_CONTENT_TYPE,
            test_plan.n_page,
            tuple(picked.aspects),
        )

    return pick(
        not is_newspaper or non_content,
        lambda: NewspaperTestTriagePlan(False, None, 0, ()),
        _overlay,
    )


def plan_newspaper_display_budget(*, generated: int) -> int:
    """Newspaper UI budget tracks cooked count and never shows a zero cap."""
    return max(int(generated), 1)


def plan_practice_concept_ready(
    *,
    existing: int,
    min_count: int = PRACTICE_CONCEPT_MIN_QUESTIONS,
) -> bool:
    """Public practice bank already has enough questions for this concept."""
    return int(existing) >= int(min_count)


def plan_coding_page_yield(count: int | None = None) -> int:
    """How many verified coding problems to cook for one programmable page."""
    n = choose(count is None, CODING_PROBLEMS_PER_PAGE, int(count or 0))
    return max(1, n)


def plan_practice_source_chunk_chars() -> int:
    """How large each Wikipedia pseudo-page is before practice generation."""
    return PRACTICE_SOURCE_CHUNK_CHARS
