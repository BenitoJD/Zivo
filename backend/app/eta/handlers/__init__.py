"""ETA handler registration bundle.

Importing this package registers all built-in handlers with the registry.
The API process imports `app.eta` (which imports this module) so that
`build_job` / `get_handler` resolve handler workload/priority on the API
side too — not just in the worker processes.
"""

from app.eta.handlers import cpu as _cpu  # noqa: F401
from app.eta.handlers import io as _io  # noqa: F401
