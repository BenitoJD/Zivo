# Each process image ships only that process's code

**Date:** 2026-08-13 · **Status:** accepted

Worker pods use `ghcr.io/benitojd/zivo-worker`, not a product HTTP image.
Dockerfiles COPY that process's package plus `backend/app`, then
`backend/scripts/slim_service_tree.py` drops FastAPI modules and entrypoints
it does not run. Auth and storage already COPY only their own package; that stays.
Product Alembic uses `zivo-migrate`. There is no `zivo-api` Deployment
([ADR 0017](0017-owned-copy-no-api-shell.md)).

**Why.** Copying the whole API tree into every process meant workers carried
routes, Next never belonged there, and Piper/fastembed sat on HTTP images that
do not speak audio. A microservice that still ships the monolith is a label, not
a split.

**What stays in which image.**

- Worker: ETA entrypoints, lease/handlers, engines they call, Piper, ffmpeg,
  fastembed. No `app/api`, no `app/main.py`, no Alembic.
- Migrate: Alembic + models. No uvicorn, no HTTP routes.
- Practice/content/study/library/admin: the route modules that process mounts.

**Considered and rejected.**

- *Keep sharing `zivo-api` for workers.* Smaller ops, but the image is the
  monolith.
- *Hand-maintain a second source tree for workers.* Drift. Slim-after-copy is
  one list of keep files per profile.
- *One pip extra per process.* Later; the first cut is unused Python modules.

**Consequence.** Deploy builds and pushes `zivo-worker`. Helm worker values set
`image.repository` to that image. Ship gates include a worker import smoke that
fails if `app.eta.worker` loads `app.api`.
