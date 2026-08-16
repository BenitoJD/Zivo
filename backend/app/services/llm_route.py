"""Llm Route Engine — pick / failover / skip.

Version: qb.llm_route.v1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.engine_runtime import Pred, Rule, first_match

LLM_ROUTE_VERSION = "qb.llm_route.v1"
DEFAULT_LLM_ROUTE_POLICY = "llm_route_v1"
LlmRouteAction = Literal["use_primary", "use_fallback", "skip"]

_RULES = (
    Rule(when=(Pred("has_primary", "falsey"), Pred("has_fallback", "falsey")), action="skip"),
    Rule(when=(Pred("primary_failed", "truthy"), Pred("has_fallback", "truthy")), action="use_fallback"),
    Rule(when=(Pred("has_primary", "falsey"), Pred("has_fallback", "truthy")), action="use_fallback"),
    Rule(when=(), action="use_primary"),
)


@dataclass(frozen=True)
class LlmRouteVerdict:
    action: LlmRouteAction
    policy: str = DEFAULT_LLM_ROUTE_POLICY
    policy_version: str = LLM_ROUTE_VERSION


def evaluate_llm_route(
    *,
    has_primary: bool,
    has_fallback: bool = False,
    primary_failed: bool = False,
    policy: str | None = None,
) -> LlmRouteVerdict:
    signals = {
        "has_primary": has_primary,
        "has_fallback": has_fallback,
        "primary_failed": primary_failed,
    }
    hit = first_match(_RULES, signals)
    return LlmRouteVerdict(action=hit.action, policy=policy or DEFAULT_LLM_ROUTE_POLICY)
