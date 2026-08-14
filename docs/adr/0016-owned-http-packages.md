# Route modules live with their HTTP process; scheduler lives with IO workers

**Date:** 2026-08-14 · **Status:** accepted

Product HTTP routers leave `backend/app/api/` and live in the owning service
package (`practice_api`, `content_api`, `study_api`, `library_api`, `admin_api`).
The ETA scheduler loop runs on the IO worker process. `zivo-api` is health plus
the Next catch-all, not a BFF and not a product router host. Docker images COPY
that process's entrypoint, then `slim_service_tree.py` drops sibling packages,
product routers, frontend, and `app.*` modules the process never imports.

**Why.** Mounting another process's routes from a shared `app.api` tree meant
every HTTP image still *contained* the monolith even after the process split.
A topic or practice image that still ships study routers and unused engines is
a label, not a split. The scheduler is job orchestration, so it belongs with
workers (`FOR UPDATE SKIP LOCKED` already allows more than one IO replica).

**What stays in which image.**

- Worker: ETA entrypoints, lease/handlers, engines they import, Piper, ffmpeg,
  fastembed. No `app/api`, no `*_api` packages, no frontend.
- API: health router, Alembic, models Alembic needs. No product routers, no
  scheduler loop, no Piper.
- Practice/content/study/library/admin: that service's `*_main.py` + `*_api`
  plus reachable `app.*` library modules. No sibling `*_api`.
- Auth/storage: their own package only. They do not COPY `backend/`.

**Not a BFF.** Next rewrites each `/api/...` prefix to the owning in-cluster
service. `zivo-api` remains so `/api/health` and the catch-all have a target.
It does not aggregate product payloads.

**Considered and rejected.**

- *Keep routers in `backend/app/api` and slim after COPY.* Images would still
  start from the whole route tree; source would still look like a monolith.
- *Tiny scheduler FastAPI.* Another process for a loop workers already run.
- *Next as the remaining BFF.* Rewrites are not aggregation; keep one web app.

**Consequence.** DNS A records for dedicated hosts stay optional until TLS.
Leave `NEXT_PUBLIC_*` empty so apex rewrites hit in-cluster services. Ship
gates fail if a worker or HTTP service loads a sibling `*_api` package, or if
a slimmed tree still contains `frontend/` or another service's routers.
