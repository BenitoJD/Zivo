# Images COPY owned packages plus the shared app library; zivo-api is gone

**Date:** 2026-08-14 · **Status:** accepted

HTTP and worker images COPY that process's directory plus `backend/app` (engines,
models, services). They do not `COPY backend/` and then slim a monolith tree.
Product Alembic runs from a slim `zivo-migrate` image used only by the db-schema
Job. There is no health-only `zivo-api` Deployment. Next rewrites each `/api/...`
prefix to the owning service; unmatched `/api/*` is a Next 404. `/health` and
`/api/health` rewrite to auth.

**Why.** Slim-after-copy still started from the whole backend tree, so images
looked like a split and shipped like a monolith. A dedicated API process whose
only job is health plus a catch-all is leftover surface. Auth already serves
health. Engines stay in-process libraries under `backend/app` because they are
shared; they do not force every image to copy tests, Alembic, or sibling routers.

**What stays in which image.**

- Practice/content/study/library/admin: that service's `*_main.py` + `*_api`,
  service Alembic, reachable `app.*` after AST prune. No sibling `*_api`.
- Worker: ETA entrypoints under `workers/`, reachable `app.*`, Piper, ffmpeg,
  fastembed. No `app/api`, no HTTP packages.
- Migrate: `app` models Alembic needs, `alembic/`, `schema/`, lock script. No
  uvicorn.
- Auth/storage: their own package only. Unchanged.

**Considered and rejected.**

- *Keep COPY backend then slim.* Forbidden leftovers stay a cleanup step instead
  of a COPY contract.
- *Duplicate engines into every service directory.* Drift. Shared `backend/app`
  is the small shared package.
- *Keep zivo-api for `/health` and the catch-all.* Auth already has health; a
  catch-all hid missing rewrites.

**Consequence.** Leave `NEXT_PUBLIC_*` empty until dedicated DNS exists so apex
rewrites hit in-cluster services. Ship gates fail if a Dockerfile copies
`backend/ /app/`, if Next still catch-alls to zivo-api, or if a slimmed tree
contains another service's routers.
