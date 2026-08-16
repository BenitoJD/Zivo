"""If-token-free decision runtime.

Engines are rule tables. Callers load signals, call first_match / evaluate_*,
then apply() a typed action. This module has no `if` / `elif` / `else` / ternary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence, TypeVar

T = TypeVar("T")

RUNTIME_VERSION = "qb.engine_runtime.v1"


@dataclass(frozen=True)
class Pred:
    key: str
    op: str = "truthy"
    value: Any = None


@dataclass(frozen=True)
class Rule:
    when: tuple[Pred, ...]
    action: str
    rationale: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StepResult:
    action: str
    payload: Any = None
    ctx: Any = None


def _lt(a: Any, b: Any) -> bool:
    return a < b


def _lte(a: Any, b: Any) -> bool:
    return a <= b


def _gt(a: Any, b: Any) -> bool:
    return a > b


def _gte(a: Any, b: Any) -> bool:
    return a >= b


def _eq(a: Any, b: Any) -> bool:
    return a == b


def _neq(a: Any, b: Any) -> bool:
    return a != b


def _truthy(a: Any, _b: Any) -> bool:
    return bool(a)


def _falsey(a: Any, _b: Any) -> bool:
    return not a


def _contains(a: Any, b: Any) -> bool:
    return b in (a or ())


def _in_set(a: Any, b: Any) -> bool:
    return a in (b or ())


def _startswith(a: Any, b: Any) -> bool:
    return a.startswith(b)


def _endswith(a: Any, b: Any) -> bool:
    return a.endswith(b)


OPS: dict[str, Callable[[Any, Any], bool]] = {
    "lt": _lt,
    "lte": _lte,
    "gt": _gt,
    "gte": _gte,
    "eq": _eq,
    "neq": _neq,
    "truthy": _truthy,
    "falsey": _falsey,
    "contains": _contains,
    "in_set": _in_set,
    "startswith": _startswith,
    "endswith": _endswith,
}

UNMATCHED = Rule(when=(), action="unmatched")


def pred_ok(pred: Pred, signals: Mapping[str, Any]) -> bool:
    return OPS[pred.op](signals[pred.key], pred.value)


def all_preds(preds: Sequence[Pred], signals: Mapping[str, Any]) -> bool:
    return all(pred_ok(pred, signals) for pred in preds)


def first_match(
    rules: Sequence[Rule],
    signals: Mapping[str, Any],
    default: Rule = UNMATCHED,
) -> Rule:
    return next(filter(lambda rule: all_preds(rule.when, signals), rules), default)


def apply(action: str, handlers: Mapping[str, Callable[..., T]], *args: Any, **kwargs: Any) -> T:
    return handlers[action](*args, **kwargs)


def choose(flag: bool, when_true: T, when_false: T) -> T:
    return {True: when_true, False: when_false}[bool(flag)]


def pick(flag: bool, when_true: Callable[[], T], when_false: Callable[[], T]) -> T:
    return {True: when_true, False: when_false}[bool(flag)]()


def run_steps(steps: Sequence[Callable[[Any], StepResult]], ctx: Any) -> Any:
    iterator = iter(steps)

    def go(current: Any) -> Any:
        try:
            step = next(iterator)
        except StopIteration:
            return current
        result = step(current)
        return apply(
            result.action,
            {
                "stop": lambda: result.payload,
                "continue": lambda: go(result.ctx),
            },
        )

    return go(ctx)
