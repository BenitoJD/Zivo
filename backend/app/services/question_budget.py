"""Question budget planner: pure functions (policy seam).

Design: docs/QUESTION_BUDGET_ENGINE.md
Version: qb.budget.v1

Deterministic core only. LLM may propose units/centrality; this module owns N.
Pipeline batch sizes (REFILL_BATCH_SIZE) live elsewhere and must not enter here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

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

# Formative Test session information floor (IRT): SE=0.40 → I*=6.25
SE_TARGET_FORMATIVE = 0.40
I_BAR_DEFAULT = 0.20


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
    if mode == "learn":
        return M_LEARN
    return M_TEST_HIGH_STAKES if high_stakes else M_TEST_FORMATIVE


def parse_budget_mode(value: str | None) -> Mode:
    """Normalize API / progress values. Anything other than test → learn."""
    return "test" if (value or "").strip().lower() == "test" else "learn"


def units_from_aspect_dicts(aspects: Sequence[dict[str, object]] | None) -> list[Unit]:
    """Build planner units from persisted page_coverage aspects."""
    out: list[Unit] = []
    for i, raw in enumerate(aspects or []):
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or f"aspect-{i + 1}")
        cent_raw = str(raw.get("centrality") or "central").strip().lower()
        centrality: Centrality
        if cent_raw in WEIGHT:
            centrality = cent_raw  # type: ignore[assignment]
        else:
            centrality = "central"
        out.append(Unit(key=key, centrality=centrality))
    return out


def coverage_score(units: Sequence[Unit], mode: Mode, *, high_stakes: bool = False) -> float:
    m = mode_multiplier(mode, high_stakes=high_stakes)
    return sum(WEIGHT[u.centrality] * m for u in units)


def density_unit_count(words: int, substantial_paragraphs: int) -> int:
    """Heuristic U_hat when triage aspects are missing (matches legacy ~1/120 words)."""
    if words <= 0:
        return 0
    by_words = max(1, words // W_PER_UNIT)
    if substantial_paragraphs > 0:
        return min(substantial_paragraphs, by_words)
    return by_words


def information_floor_items(
    *,
    se_target: float = SE_TARGET_FORMATIVE,
    i_bar: float = I_BAR_DEFAULT,
) -> int:
    """N_info = ceil(I* / Ĩ) with I* = 1/SE². Session/document Test floor, not per-page."""
    if se_target <= 0 or i_bar <= 0:
        raise ValueError("se_target and i_bar must be positive")
    i_star = 1.0 / (se_target * se_target)
    return int(math.ceil(i_star / i_bar))


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
    if non_content:
        return PageBudgetPlan(
            n_page=0,
            n_cov=0.0,
            mode=mode,
            confidence=confidence or "high",
        )

    if units:
        n_cov = coverage_score(units, mode, high_stakes=high_stakes)
        conf: Literal["high", "medium", "low"] = confidence or "high"
    else:
        u_hat = density_unit_count(words, substantial_paragraphs)
        synthetic = [Unit(key=f"density-{i}", centrality="central") for i in range(u_hat)]
        n_cov = coverage_score(synthetic, mode, high_stakes=high_stakes)
        conf = confidence or ("medium" if u_hat else "high")

    n_page = int(round(n_cov))
    n_page = max(0, n_page)
    if ceil_page is not None:
        n_page = min(ceil_page, n_page)
    return PageBudgetPlan(n_page=n_page, n_cov=n_cov, mode=mode, confidence=conf)


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
    n_info = 0
    if mode == "test":
        # High-stakes uses tighter SE (0.30) unless caller overrides se_target.
        if high_stakes and se_target == SE_TARGET_FORMATIVE:
            se_target = 0.30
        n_info = information_floor_items(se_target=se_target, i_bar=i_bar)
        n_doc = max(n_doc_cov, n_info)
    else:
        n_doc = n_doc_cov
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
    if coverage_complete:
        return True
    return int(generated) >= max(0, int(budget))


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
    if non_content:
        return 0
    if newspaper_test:
        return max(0, int(newspaper_test_n))
    if units and serve_mode != persisted_mode:
        return plan_page_budget(units, mode=serve_mode, confidence=confidence).n_page
    if stored_budget is None:
        if missing == "omit":
            return None
        return speculative_page_budget(mode=serve_mode).n_page
    return max(0, int(stored_budget))
