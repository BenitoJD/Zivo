from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

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
    if handler and handler.input_model:
        model = handler.input_model.model_validate(payload)
        return model.model_dump(mode="json")
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json")
    return payload


def normalize_result(handler: EtaHandler | None, result: Any) -> dict[str, Any]:
    if handler and handler.output_model:
        model = handler.output_model.model_validate(result)
        return model.model_dump(mode="json")
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    return result
