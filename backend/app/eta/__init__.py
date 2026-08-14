"""ETA job queue subsystem.

Importing this package registers all built-in handlers (cpu + io workloads)
and exposes the in-process scheduler registry. Worker processes import `app.eta`
so `get_handler(name)` and scheduler definitions resolve where jobs run. The IO
worker also starts the scheduler loop.
"""

from app.eta import handlers as _handlers  # noqa: F401
from app.eta.scheduler_registry import (
    eta_scheduler,
    get_scheduler_definition,
    list_scheduler_definitions,
)

__all__ = [
    "eta_scheduler",
    "get_scheduler_definition",
    "list_scheduler_definitions",
]
