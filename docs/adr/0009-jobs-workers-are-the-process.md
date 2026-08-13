# ETA workers are the jobs microservice

**Date:** 2026-08-13 · **Status:** accepted

Zivo already runs jobs as a separate process: Helm releases `zivo-worker-io` and
`zivo-worker-cpu` (`zivo-worker` image, `run_eta_worker_*.py`). Lease,
heartbeat, reclaim, and handler execution of `qb.jobs` live in those pods. That
process is the jobs microservice. There is no extra FastAPI that shares the
table.

**Why.** Uploads and identity had a single write-owner we could move with the
HTTP. Jobs do not: product routes *and* workers INSERT `qb.jobs` (enqueue),
while only workers lease. A jobs HTTP service that wraps enqueue while workers
still lease the same rows would be two writers with no isolation win. Moving
*both* enqueue and lease into one writer is a worker rewrite, not this slice.

**Not in this slice.** A `jobs/` FastAPI, a `jobs.zivo.fyi` hostname, splitting
learn/grade/chat, or one process per engine ([ADR 0004](0004-swappable-policy-seam.md)).

**Considered and rejected.**

- *Empty jobs FastAPI sharing `qb.jobs`.* Two writers; a fake split.
- *Move enqueue only.* Workers would still lease; blast radius unchanged.
- *One process per engine.* Contradicts [ADR 0004](0004-swappable-policy-seam.md).

**Consequence.** Product API and workers both write `qb.jobs` until enqueue
moves behind HTTP to the worker process. Do not add a jobs chart that is not
that writer. Engines stay in-process libraries.
