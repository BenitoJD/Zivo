"""Shared stale-running job thresholds for ETA workers and recovery paths."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

STALE_RUNNING_TIMEOUT_SECONDS = float(os.getenv("ETA_STALE_RUNNING_TIMEOUT", "600"))
STALE_REAPER_INTERVAL_SECONDS = float(os.getenv("ETA_STALE_REAPER_INTERVAL", "60"))


def stale_running_cutoff(now: datetime | None = None) -> datetime:
    """Jobs locked before this instant are treated as orphaned (worker died)."""
    now = now or datetime.now(timezone.utc)
    return now - timedelta(seconds=STALE_RUNNING_TIMEOUT_SECONDS)
