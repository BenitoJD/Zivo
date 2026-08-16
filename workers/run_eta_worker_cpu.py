"""CPU ETA worker entrypoint.

Installs SIGTERM/SIGINT handlers so a rolling deploy / HPA scale-down / pod
eviction drains in-flight jobs instead of orphaning them (which would force the
~90s lease-expiry reclaim path). The drain itself lives in run_eta_worker(); we
only flip a flag here.
"""

from __future__ import annotations

import logging
import signal
import threading

from app.engine_runtime import pick
from app.eta.worker import run_eta_worker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    stop_event = threading.Event()

    def _request_stop(signum: int, *_args) -> None:  # noqa: ARG001
        logger.info("CPU ETA worker received signal %s — draining", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    run_eta_worker(should_stop=stop_event.is_set)


def _cli() -> None:
    main()


pick(__name__ == "__main__", _cli, lambda: None)
