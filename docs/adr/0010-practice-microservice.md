# Practice banks are the third HTTP microservice

**Date:** 2026-08-13 · **Status:** accepted

Zivo splits coding-bank, system-design-bank, Wikidata practice, and newspaper
practice catalog HTTP out of the product API into `practice/` (`practice.zivo.fyi`,
Helm `zivo-practice`, local `:8203`). Engines stay imported libraries. Workers
still generate coding facets and ingest editions.

**Why.** Same bar as [ADR 0007](0007-auth-microservice.md) and
[ADR 0008](0008-storage-microservice.md): own process, hostname, Alembic version
table (`alembic_version_practice`), and exclusive `practice` schema marker.
Product API stops serving those routes so there is one HTTP writer for them.

**Data.** Shared `qb.*` / `intel.*` stay on product Alembic. Moving
`qb.coding_assertion_facets` or `qb.newspaper_edition` here would create two
writers (this process plus workers). Workers stay workers.

**Cookie.** JWT + `auth.account.session_version`, same as storage. Guest mint
stays on the product API.

**Not in practice.** Newspaper admin (channel / brands) is content.
Learn/grade/chat is study. Job lease stays on workers ([ADR 0009](0009-jobs-workers-are-the-process.md)).

**Considered and rejected.**

- *Move coding facet tables into `practice.*`.* Workers still INSERT them.
- *One process per engine.* Contradicts [ADR 0004](0004-swappable-policy-seam.md).
- *Shared Python package for routers.* Would become a distributed monolith;
  this service imports backend as a library and mounts a subset of routers.

**Consequence.** DNS needs a `practice.zivo.fyi` A record before TLS. Until then
keep public URLs empty and rewrite `/api/coding`, `/api/system-design`,
`/api/practice`, and `/api/newspaper` (non-admin) on apex to in-cluster
`zivo-practice`.
