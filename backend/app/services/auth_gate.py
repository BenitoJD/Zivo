"""Auth Gate Engine — allow / deny / redirect / guest.

Version: qb.auth_gate.v1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.engine_runtime import Pred, Rule, first_match

AUTH_GATE_VERSION = "qb.auth_gate.v1"
DEFAULT_AUTH_GATE_POLICY = "auth_gate_v1"
AuthGateAction = Literal["allow", "deny", "redirect", "guest"]

_RULES = (
    Rule(when=(Pred("has_account", "falsey"), Pred("allow_guest", "falsey")), action="redirect"),
    Rule(when=(Pred("has_account", "falsey"), Pred("allow_guest", "truthy")), action="guest"),
    Rule(when=(Pred("is_admin_route", "truthy"), Pred("is_admin", "falsey")), action="deny"),
    Rule(when=(), action="allow"),
)


@dataclass(frozen=True)
class AuthGateVerdict:
    action: AuthGateAction
    policy: str = DEFAULT_AUTH_GATE_POLICY
    policy_version: str = AUTH_GATE_VERSION


def evaluate_auth_gate(
    *,
    account: Any | None,
    is_admin: bool = False,
    is_admin_route: bool = False,
    allow_guest: bool = False,
    policy: str | None = None,
) -> AuthGateVerdict:
    signals = {
        "has_account": account is not None,
        "is_admin": is_admin,
        "is_admin_route": is_admin_route,
        "allow_guest": allow_guest,
    }
    hit = first_match(_RULES, signals)
    return AuthGateVerdict(action=hit.action, policy=policy or DEFAULT_AUTH_GATE_POLICY)
