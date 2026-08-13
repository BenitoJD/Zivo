# Library (sources / documents) is the sixth HTTP microservice

**Date:** 2026-08-13 · **Status:** accepted

Zivo splits source ingest, documents, activity status, and audiobook HTTP out of
the product API into `library/` (`library.zivo.fyi`, Helm `zivo-library`, local
`:8206`). Workers still parse, cook, and render audio. The library process is the
HTTP writer for those routes.

**Why.** After practice/content/study left, the remaining public product surface
was still library + admin + guest. Library is the upload/source owner. Guest mint
moved with study because workspace reads already live there.

**Data.** Shared `qb.*` / `intel.*` stay on product Alembic.
`alembic_version_library` owns the exclusive `library` schema marker.

**Not in library.** Models and debug (admin). Guest, offline packs, and reference
(study). Job lease stays on workers ([ADR 0009](0009-jobs-workers-are-the-process.md)).

**Considered and rejected.**

- *Keep documents on `zivo-api`.* Would leave a fat public API after the other
  HTTP slices moved.
- *Move parse tables into `library.*`.* Workers still INSERT them.

**Consequence.** DNS needs a `library.zivo.fyi` A record before TLS. Until then
apex rewrites `/api/sources`, `/api/documents`, `/api/activities`, and
`/api/audiobook` to in-cluster `zivo-library`.
