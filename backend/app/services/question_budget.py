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

CEIL_PAGE = 40
W_PER_UNIT = 120
SESSION_SOFT = 20

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


def plan_page_budget(
    units: Sequence[Unit] | None,
    *,
    mode: Mode = "learn",
    non_content: bool = False,
    high_stakes: bool = False,
    words: int = 0,
    substantial_paragraphs: int = 0,
    ceil_page: int = CEIL_PAGE,
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
    n_page = max(0, min(ceil_page, n_page))
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
