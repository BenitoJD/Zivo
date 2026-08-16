"""Presence Engine — missing / empty / ok.

Version: qb.presence.v1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.engine_runtime import Pred, Rule, first_match

PRESENCE_VERSION = "qb.presence.v1"
DEFAULT_PRESENCE_POLICY = "presence_v1"
PresenceAction = Literal["missing", "empty", "ok"]

_RULES = (
    Rule(when=(Pred("is_none", "truthy"),), action="missing", rationale="value is None"),
    Rule(when=(Pred("is_empty", "truthy"),), action="empty", rationale="value is empty"),
    Rule(when=(), action="ok", rationale="value is present"),
)


@dataclass(frozen=True)
class PresenceVerdict:
    action: PresenceAction
    policy: str = DEFAULT_PRESENCE_POLICY
    policy_version: str = PRESENCE_VERSION
    rationale: str = ""


def _is_empty(value: Any) -> bool:
    return value == "" or value == () or value == [] or value == {} or value == set()


def evaluate_presence(value: Any, *, policy: str | None = None) -> PresenceVerdict:
    signals = {"is_none": value is None, "is_empty": _is_empty(value)}
    hit = first_match(_RULES, signals)
    pol = policy or DEFAULT_PRESENCE_POLICY
    return PresenceVerdict(action=hit.action, policy=pol, rationale=hit.rationale)
