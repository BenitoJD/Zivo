## Restart-resilience: close the lease-bypass gaps

### Context — the core system is already built (in your working tree)

You already have a **lease/heartbeat crash-recovery system** for the ETA workers — exactly the "in-flight jobs survive a restart" feature you want:

- Workers renew a lease (`heartbeat_at` + `lease_deadline`) every ~15s while a job runs.
- On restart/crash/deploy, the lease expires (~90s) and a reaper requeues the orphaned `running` job — **no false reclaims on long jobs**.
- A scheduler-driven reclaim (`jobs_reclaim.py`, every 2 min) recovers orphans **even if every worker pod is down**.
- SIGTERM handlers + K8s `terminationGracePeriodSeconds: 120` let deploys **drain** in-flight work gracefully.

Migration `042_job_lease_heartbeat`, the `Job` model fields, both workers, tests, K8s yaml, and `.env.example` are all in place. **Gates currently pass**: ruff clean, import OK, 807 unit tests pass, 22/24 integration tests pass (the 2 failures are pre-existing + environmental — a real queued `ingest.rag_window` job on the shared dev DB; unrelated to this work).

### The problem — 4 older paths still use the old `locked_at`-only model and bypass the lease

The whole point is *"no false reclaims on long-running live jobs."* These paths break that promise:

1. **`_reclaim_stale_generate_jobs`** (`question_pool_jobs.py:748`) — called by the CPU worker every ~60s. Force-requeues any `generate.questions` job whose `locked_at` is >1800s old, **ignoring a fresh heartbeat**. A long LLM generation (>30 min, actively heartbeating) gets requeued while the original worker is still running it → **split-brain / double execution**. It also leaves stale `lease_deadline`/`heartbeat_at`/`locked_by` behind on requeue.

2. **Liveness SQL** in 3 recovery paths decides "is there a live ingest job?" via `locked_at >= cutoff`. Under the lease model a job can legitimately run far past `locked_at`'s 600s window, so these see a **live long job as dead** and enqueue **duplicate** recovery work:
   - `rag_window.py:_HAS_ACTIVE_INGEST_JOBS_SQL` (`has_active_ingest_jobs`)
   - `schedules/ingest_recovery.py:_recover_stuck_indexing_documents`
   - `schedules/newspaper.py:_recover_stuck_editions`

### Changes

**1. Remove the redundant `_reclaim_stale_generate_jobs` tick from the CPU worker** — `backend/app/eta/worker.py`
- Delete the `reclaim_tick` counter and the `if reclaim_tick % 200 == 0:` block that calls it.
- The shared reaper (`reclaim_stale_jobs_sync`, already running on startup + every 60s in the worker + every 2 min via scheduler) now reclaims orphaned `generate.questions` jobs with **lease-awareness** and **preserves `result`/checkpoint** (it doesn't clear it). This also reclaims in ~90s instead of 1800s — strictly better recovery.
- Eliminates the split-brain path.

**2. Make `_reclaim_stale_generate_jobs` lease-aware** — `backend/app/services/question_pool_jobs.py`
- Keep the function (it's re-exported and offers a **document-scoped** reclaim the workload-scoped shared reaper doesn't), but fix the latent bug: add the lease predicate so a fresh-heartbeat job is never reclaimed.
  ```sql
  AND (
    lease_deadline IS NOT NULL AND lease_deadline < NOW()
    OR (lease_deadline IS NULL AND locked_at < :cutoff)   -- legacy fallback
  )
  ```
- On requeue, also clear `lease_deadline`, `heartbeat_at`, `locked_by` (keeps `result`/checkpoint).

**3. Unify liveness SQL with the lease model (single source of truth)** — `backend/app/eta/stale_jobs.py` + the 3 recovery sites
- Export a shared raw-SQL fragment `ACTIVE_JOB_LIVENESS_SQL` in `stale_jobs.py` — the **inverse** of the orphan clause. A `running` job is "active" if its lease is still valid (`lease_deadline >= NOW()`), or (legacy fallback) `locked_at >= cutoff`:
  ```sql
  j.status = 'queued'
  OR (j.status = 'running' AND (
        j.lease_deadline IS NOT NULL AND j.lease_deadline >= NOW()
        OR (j.lease_deadline IS NULL AND j.locked_at IS NOT NULL AND j.locked_at >= :stale_cutoff)
      ))
  ```
- Use it in `rag_window._HAS_ACTIVE_INGEST_JOBS_SQL`, `ingest_recovery._recover_stuck_indexing_documents`, and `newspaper._recover_stuck_editions` (all already alias jobs as `j` and pass `:stale_cutoff`).
- Result: a long, live, heartbeating job is correctly seen as active → no duplicate rag_window/page-ingest recovery.

### Tests
- Add a unit test asserting `_reclaim_stale_generate_jobs` does **not** touch a `generate.questions` job with a valid (unexpired) lease, and **does** clear the lease fields when it requeues an expired one.
- Add a unit test for `has_active_ingest_jobs` (or the shared fragment) confirming a valid-lease long job counts as active.
- Existing `test_reclaim_does_not_touch_live_heartbeat_job` already covers the shared reaper; confirm it still passes.

### Verification (run before commit)
- `./scripts/ship-gates.sh backend` (ruff + import + unit tests)
- `backend/scripts/test_alembic_migrations.sh` (confirm `042` applies cleanly)
- `pytest tests/integration/test_eta_worker.py` (lease/reclaim tests; the 2 pre-existing environmental failures are unrelated and out of scope)

No schema change is needed beyond the already-written migration `042`. No frontend change.