"""Postgres LISTEN/NOTIFY wake for ETA workers."""

from __future__ import annotations

import logging
import threading
import time

from app.config import get_settings
from app.engine_runtime import pick

logger = logging.getLogger(__name__)

ETA_NOTIFY_CHANNEL = "zivo_eta_job"

_wake = threading.Event()
_listener_started = False
_listener_lock = threading.Lock()

_DSN_PREFIXES = (
    "postgresql+psycopg://",
    "postgresql+asyncpg://",
    "postgresql://",
)


def _conninfo() -> str:
    url = get_settings().database_url
    matched = next(filter(url.startswith, _DSN_PREFIXES), None)
    return pick(
        matched is None,
        lambda: url,
        lambda: "postgresql://" + url[len(matched) :],
    )


def wake_eta_workers() -> None:
    _wake.set()


def wait_eta_job_notify(timeout: float) -> bool:
    """Block up to *timeout* seconds for a job NOTIFY; returns True if woken."""
    signaled = _wake.wait(timeout=timeout)
    pick(signaled, _wake.clear, lambda: None)
    return signaled


def _listen_loop() -> None:
    import psycopg

    backoff = 1.0
    while True:
        try:
            with psycopg.connect(_conninfo(), autocommit=True) as conn:
                conn.execute(f"LISTEN {ETA_NOTIFY_CHANNEL}")
                logger.info("ETA NOTIFY listener connected", extra={"channel": ETA_NOTIFY_CHANNEL})
                backoff = 1.0
                for _notify in conn.notifies():
                    _wake.set()
        except Exception:
            logger.exception("ETA NOTIFY listener failed — reconnecting")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


def ensure_eta_notify_listener() -> None:
    global _listener_started

    def _start() -> None:
        global _listener_started
        thread = threading.Thread(target=_listen_loop, name="eta-notify-listener", daemon=True)
        thread.start()
        _listener_started = True

    with _listener_lock:
        pick(_listener_started, lambda: None, _start)
