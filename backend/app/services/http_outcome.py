"""Http Outcome Engine — domain action → status + body.

Version: qb.http_outcome.v1
"""

from __future__ import annotations

from dataclasses import dataclass

from app.engine_runtime import Pred, Rule, first_match

HTTP_OUTCOME_VERSION = "qb.http_outcome.v1"
DEFAULT_HTTP_OUTCOME_POLICY = "http_outcome_v1"

_RULES = (
    Rule(when=(Pred("action", "eq", "missing"),), action="not_found", extras={"status": 404}),
    Rule(when=(Pred("action", "eq", "empty"),), action="not_found", extras={"status": 404}),
    Rule(when=(Pred("action", "eq", "deny"),), action="forbidden", extras={"status": 403}),
    Rule(when=(Pred("action", "eq", "redirect"),), action="unauthorized", extras={"status": 401}),
    Rule(when=(Pred("action", "eq", "guest"),), action="ok", extras={"status": 200}),
    Rule(when=(Pred("action", "eq", "invalid"),), action="unprocessable", extras={"status": 422}),
    Rule(when=(Pred("action", "eq", "conflict"),), action="conflict", extras={"status": 409}),
    Rule(when=(), action="ok", extras={"status": 200}),
)


@dataclass(frozen=True)
class HttpOutcomeVerdict:
    action: str
    status: int
    policy: str = DEFAULT_HTTP_OUTCOME_POLICY
    policy_version: str = HTTP_OUTCOME_VERSION


def evaluate_http_outcome(action: str, *, policy: str | None = None) -> HttpOutcomeVerdict:
    hit = first_match(_RULES, {"action": action})
    return HttpOutcomeVerdict(
        action=hit.action,
        status=int(hit.extras["status"]),
        policy=policy or DEFAULT_HTTP_OUTCOME_POLICY,
    )
