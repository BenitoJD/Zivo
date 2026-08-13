# Study (learn / grade / chat) is the fifth HTTP microservice

**Date:** 2026-08-13 · **Status:** accepted

Zivo splits learn-queue, MCQ serve/grade, assertions, progress, artifacts
workspace, and tutor chat out of the product API into `study/` (`study.zivo.fyi`,
Helm `zivo-study`, local `:8205`). Guest mint, offline packs, and reference live
on study with the rest of the learner loop. Chat stays with study because it
shares artifact + chunk context.

**Why.** The remaining product API was still the whole question loop. An honest
HTTP split is this process, not a fifth chat service and not one process per
engine ([ADR 0004](0004-swappable-policy-seam.md)).

**Data.** Shared `qb.*` / `intel.*` stay on product Alembic. Workers still cook
artifacts and persist generated assertions. Study is the only HTTP writer for
learn-queue, grade, chat, progress, and artifact workspace reads/updates.
`alembic_version_study` owns the exclusive `study` schema marker.

**Not in study.** Sources, documents, activities, audiobook (library). Models and
debug (admin).

**Considered and rejected.**

- *Split chat into its own service.* Threads and retrieval are artifact-scoped;
  a fifth writer of the same context is not an honest boundary.
- *Move `qb.artifact` into `study.*`.* Workers still INSERT cooked artifacts.

**Consequence.** DNS needs a `study.zivo.fyi` A record before TLS. Until then
apex rewrites `/api/artifacts`, `/api/mcq`, `/api/chat`, `/api/progress`,
`/api/assertions`, `/api/guest`, `/api/offline`, and `/api/reference` to
in-cluster `zivo-study`.
