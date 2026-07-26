## Cluster-wide rate limiter (Postgres-backed)

**Goal:** Replace the per-process in-memory limiter with a cluster-safe Postgres-backed one, so the effective limit is `rate_limit_per_minute` regardless of how many API pods HPA scales to (2-4 today). The current per-pod counter means a real client can do `60 × N_replicas` requests/min instead of 60.

### Approach
Reuse the existing cluster-safe atomic-counter pattern from `backend/app/services/usage.py` (`_atomic_increment_demo`: `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`). No new infra — Postgres is already there, PgBouncer transaction-pooling is fine for one-shot atomic SQL, and the 41 rate-limited routes are already DB-heavy (chat, generate, upload), so one cheap upsert is noise.

### Changes

**1. Migration — `backend/alembic/versions/033_rate_limit_hits.py`** (chains off `032_seo_learn_content`)
New table:
```sql
CREATE TABLE qb.rate_limit_hit (
  bucket      INTEGER NOT NULL,          -- floor(unix_epoch / 60): fixed minute bucket
  client_key  VARCHAR(80) NOT NULL,      -- client IP (CIDR-resolved via request_ip)
  hit_count   INTEGER NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT rate_limit_hit_pk PRIMARY KEY (bucket, client_key)
);
```
Plus an `upgrade`/`downgrade`. Baselined into `backend/schema/qb_app.sql`.

**2. `backend/app/services/rate_limit.py`** — rewrite the check to use a fixed-minute-bucket upsert:
```python
def rate_limit(request: Request, db: Session = Depends(get_db)) -> None:
    settings = get_settings()
    if settings.rate_limit_per_minute <= 0: return
    key = client_ip(request)
    bucket = int(time.time()) // 60      # fixed minute; sliding within the minute
    count = db.execute(text("""
        INSERT INTO qb.rate_limit_hit (bucket, client_key, hit_count)
        VALUES (:bucket, :key, 1)
        ON CONFLICT (bucket, client_key)
        DO UPDATE SET hit_count = qb.rate_limit_hit.hit_count + 1
        RETURNING hit_count
    """), {"bucket": bucket, "key": key}).scalar()
    db.commit()
    if count > settings.rate_limit_per_minute:
        raise HTTPException(429, "Rate limit exceeded")
```
- Keep `rate_limit_dependency` wrapper so route declarations don't change.
- Window sweep: a throttled "one sweep per ~5 min per pod" using a module-level monotonic timestamp, `DELETE WHERE bucket < (now//60) - 2`. Cheap, keeps the table tiny (≤ a few hundred rows per active minute).

**3. `backend/app/config.py`** — no new knob needed; `rate_limit_per_minute` (line 27) already drives both paths. Limiter always uses Postgres when `rate_limit_per_minute > 0` (same enable condition as today).

**4. Fallback policy (fail-open on DB error):** if the upsert raises (DB transient blip), log + allow the request through, never 500 the user over rate-limit bookkeeping. Standard choice — availability over perfect throttling for a non-critical counter.

**5. Tests:**
- Unit: `backend/tests/unit/test_rate_limit.py` — bucket math, the >limit → 429 path, the fail-open path (mocked DB error), sweep throttling. Uses mock DB like the existing `test_usage_ip_hash.py`.
- Integration (DB-required, skipped if dev DB unreachable, like `test_learn_queue_api.py`): verify cluster-shared counting — two `rate_limit()` calls under the same key (simulating two pods) hit the same counter and the (N+1)th trips 429.
- Keep `test_request_ip.py` (already green) — it tests `client_ip`, unaffected.

**6. Prod config:** no secret/env change needed — the limiter reads the existing `rate_limit_per_minute` (default 60) and uses the same `DATABASE_URL` already in `zivo-secrets`. The migration runs in the deploy's `alembic-migrate` Job before the new pods roll.

### Verification plan (post-deploy)
- Re-run the prod limiter probe: with 2 API pods, the 429 should now fire at request **#61**, not #121 (cluster-wide limit of 60, not 60×2).
- Confirm `qb.rate_limit_hit` rows accumulate and the sweep keeps the table small: `SELECT COUNT(*), MAX(bucket) FROM qb.rate_limit_hit;` from prod postgres-0.
- All existing routes (41) keep working; 429 still appears in prod logs.
- Full test suite (currently 737) stays green.

### Rollback
- Alembic `downgrade -1` drops the table.
- If the Postgres path misbehaves in prod, setting `RATE_LIMIT_PER_MINUTE=0` disables the limiter entirely (existing behavior) — no code rollback needed for safety.

### Out of scope
- No Redis, no new Helm chart, no new dev dependencies. Docker-compose, dev.sh, and prod charts are untouched.
- The `rate_limit_per_minute` value (60) stays the same — this change makes the *existing* limit actually hold cluster-wide, it doesn't change the number.

### Files touched
- `backend/alembic/versions/033_rate_limit_hits.py` (new)
- `backend/schema/qb_app.sql` (baseline the new table)
- `backend/app/services/rate_limit.py` (rewrite check, keep `rate_limit_dependency`)
- `backend/tests/unit/test_rate_limit.py` (new)
- `backend/tests/integration/test_rate_limit_db.py` (new, DB-gated)

No frontend changes. Deploy: commit + push + `gh workflow run deploy.yml -f branch=main` + monitor (same as the last two deploys), then verify the 429-at-61 behavior in prod.