"""Daily eviction of stale LLM response-cache rows.

qb.llm_response_cache grows with every cached chat reply; without eviction the
HNSW index and table bloat and every cache lookup slows. This schedule deletes
rows older than settings.response_cache_ttl_days once a day. Runs on the
in-process ETA scheduler thread (no separate worker needed) — the DELETE is
cheap and indexed (llm_response_cache_created_idx).
"""

from __future__ import annotations

from app.config import get_settings
from app.db import SessionLocal
from app.eta.scheduler_registry import eta_scheduler
from app.services.response_cache import purge_stale_cache


@eta_scheduler(every_minutes=60 * 24, key="cache.purge")
def purge_stale_responses() -> None:
    ttl = get_settings().response_cache_ttl_days
    if ttl <= 0:
        return
    with SessionLocal() as db:
        purge_stale_cache(db, max_age_days=ttl)
