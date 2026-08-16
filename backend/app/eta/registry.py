from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.engine_runtime import Pred, Rule, apply, first_match
from app.models import JobPriority, JobWorkload


@dataclass(frozen=True)
class EtaHandler:
    name: str
    fn: Callable[[Any], Any]
    input_model: type[BaseModel] | None
    output_model: type[BaseModel] | None
    workload: JobWorkload
    priority: JobPriority


_REGISTRY: dict[str, EtaHandler] = {}

_NORMALIZE_RULES = (
    Rule(when=(Pred("has_model", "truthy"),), action="typed"),
    Rule(when=(Pred("is_model", "truthy"),), action="dump"),
    Rule(when=(), action="raw"),
)


def eta(
    *,
    name: str,
    workload: JobWorkload = JobWorkload.io,
    priority: JobPriority = JobPriority.MEDIUM,
    input_model: type[BaseModel] | None = None,
    output_model: type[BaseModel] | None = None,
) -> Callable[[Callable[[Any], Any]], Callable[[Any], Any]]:
    def decorator(fn: Callable[[Any], Any]) -> Callable[[Any], Any]:
        _REGISTRY[name] = EtaHandler(
            name=name,
            fn=fn,
            input_model=input_model,
            output_model=output_model,
            workload=workload,
            priority=priority,
        )
        return fn

    return decorator


def get_handler(name: str) -> EtaHandler | None:
    return _REGISTRY.get(name)


def list_handlers() -> dict[str, EtaHandler]:
    return dict(_REGISTRY)


def normalize_payload(handler: EtaHandler | None, payload: Any) -> dict[str, Any]:
    def _typed() -> dict[str, Any]:
        model = handler.input_model.model_validate(payload)
        return model.model_dump(mode="json")

    hit = first_match(
        _NORMALIZE_RULES,
        {
            "has_model": bool(handler and handler.input_model),
            "is_model": isinstance(payload, BaseModel),
        },
    )
    return apply(
        hit.action,
        {
            "typed": _typed,
            "dump": lambda: payload.model_dump(mode="json"),
            "raw": lambda: payload,
        },
    )


def normalize_result(handler: EtaHandler | None, result: Any) -> dict[str, Any]:
    def _typed() -> dict[str, Any]:
        model = handler.output_model.model_validate(result)
        return model.model_dump(mode="json")

    hit = first_match(
        _NORMALIZE_RULES,
        {
            "has_model": bool(handler and handler.output_model),
            "is_model": isinstance(result, BaseModel),
        },
    )
    return apply(
        hit.action,
        {
            "typed": _typed,
            "dump": lambda: result.model_dump(mode="json"),
            "raw": lambda: result,
        },
    )
