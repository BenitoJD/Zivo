# Admin (models / debug) is the seventh HTTP microservice

**Date:** 2026-08-13 · **Status:** accepted

Zivo splits LLM catalog and debug-bank HTTP out of the product API into `admin/`
(`admin.zivo.fyi`, Helm `zivo-admin`, local `:8207`). The product API keeps health
and the ETA scheduler only.

**Why.** Models and debug are operator surfaces, not the learner loop. They do not
belong on the same process as health/scheduler, and they do not belong in library.

**Data.** Shared `qb.*` stay on product Alembic. `alembic_version_admin` owns the
exclusive `admin` schema marker.

**Not in admin.** Sources/documents (library). Practice banks. Study/grade/chat.

**Considered and rejected.**

- *Leave models on `zivo-api`.* The API would still be a mixed public+admin
  process.
- *Fold debug into study.* Debug curation is an admin workflow, not learn/grade.

**Consequence.** DNS needs an `admin.zivo.fyi` A record before TLS. Until then
apex rewrites `/api/models` and `/api/debug` to in-cluster `zivo-admin`.
