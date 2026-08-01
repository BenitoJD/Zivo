## Crash recovery for the ETA job queue

### Root causes (verified)
Your stale-reaper already recovers crashed pods, but has 5 defects: (1) time-guessed at 600s so long jobs get false-reclaimed mid-flight; (2) no graceful shutdown so every deploy orphans in-flight jobs; (3) CPU reaper fails exhausted orphans but IO reaper requeues forever; (4) `attempts` is burned on reclaim so a job orphaned 3× is auto-failed without ever erroring; (5) reaper only runs inside worker pods, so if all workers are down nothing reaps.

### Fix (industry-standard heartbeat+lease pattern)
1. **Schema** — `backend/app/models/__init__.py` + new migration `042_job_lease_heartbeat.py`: add `heartbeat_at`, `lease_deadline` to `qb.jobs` + partial index on `(status, lease_deadline) where status='running'`. Additive, backwards-compatible.
2. **Lease primitive** — new `backend/app/eta/lease.py`: `claim_lease`, `renew_lease` (heartbeat every 15s), `release_lease`; constants `LEASE_DURATION=120s`, `HEARTBEAT_INTERVAL=15s`, `STALE_THRESHOLD=90s`.
3. **Unified heartbeat-keyed reaper** — consolidate CPU/IO reapers into `backend/app/eta/stale_jobs.py`: `reclaim_stale_jobs(db, workloads)` keys off `lease_deadline`+`heartbeat_at`, fails exhausted orphans in BOTH workers (fixes #3), does NOT increment `attempts` (fixes #4).
4. **Heartbeat in workers** — `backend/app/eta/worker.py` (CPU) and `worker_async.py` (IO): claim sets `lease_deadline`; handler runs a heartbeat thread/task; release clears it.
5. **Graceful drain** — `run_eta_worker_cpu.py` + `run_eta_worker_async.py`: install SIGTERM/SIGINT handlers setting a stop flag wired to `should_stop`; existing drain logic becomes live (fixes #2).
6. **Scheduler-driven reclaim** — new `backend/app/eta/schedules/jobs_reclaim.py` (every 2 min, all workloads) so recovery doesn't need a worker pod alive (fixes #5); registered via `schedules/__init__.py`.
7. **K8s** — `infra/k8s/charts/worker/templates/deployment.yaml`: add `terminationGracePeriodSeconds: 120`.
8. **Config** — `backend/.env.example`: document the 3 new env vars; keep `ETA_STALE_RUNNING_TIMEOUT` as legacy fallback.
9. **Tests** — extend `backend/tests/integration/test_eta_worker.py`: heartbeat prevents false reclaim; orphan past deadline is requeued; exhausted orphan fails in both workers; reclaim doesn't touch `attempts`; drain stops reservations; scheduler recovers with no worker.

### Constants
LEASE_DURATION=120s, HEARTBEAT_INTERVAL=15s, STALE_THRESHOLD=90s, reclaim cadence=2min, terminationGracePeriodSeconds=120.

### Not changed
`generate.questions` 30-min resume path (complementary); document-level recovery schedules (orthogonal); `FOR UPDATE SKIP LOCKED` claim (lease layered on top).

### Ship gates
`./scripts/ship-gates.sh`, `cd backend && ./scripts/test_alembic_migrations.sh`, `cd frontend && npm run build && npm run lint`.

### Rollout
Additive columns + backwards-compatible reclaim (tolerates NULL heartbeat by falling back to `locked_at`) → single deploy; scheduler reclaim covers the transition. Verify: `running` rows show fresh `heartbeat_at`, no false reclaims on long jobs.