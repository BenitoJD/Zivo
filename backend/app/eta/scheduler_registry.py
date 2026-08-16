from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.engine_runtime import pick


@dataclass(frozen=True)
class EtaSchedulerDefinition:
    key: str
    fn: Callable[[], None]
    every_minutes: int

    def next_run_after(self, now: datetime) -> datetime:
        return now + timedelta(minutes=self.every_minutes)


_SCHEDULER_REGISTRY: dict[str, EtaSchedulerDefinition] = {}


def eta_scheduler(
    *,
    every_minutes: int,
    key: str | None = None,
) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def _bad_interval() -> None:
        raise ValueError("every_minutes must be at least 1")

    pick(every_minutes < 1, _bad_interval, lambda: None)

    def decorator(fn: Callable[[], None]) -> Callable[[], None]:
        schedule_key = key or f"{fn.__module__}.{fn.__name__}"
        _SCHEDULER_REGISTRY[schedule_key] = EtaSchedulerDefinition(
            key=schedule_key,
            fn=fn,
            every_minutes=every_minutes,
        )
        return fn

    return decorator


def get_scheduler_definition(key: str) -> EtaSchedulerDefinition | None:
    return _SCHEDULER_REGISTRY.get(key)


def list_scheduler_definitions() -> dict[str, EtaSchedulerDefinition]:
    return dict(_SCHEDULER_REGISTRY)
