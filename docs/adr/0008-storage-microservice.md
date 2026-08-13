# Object storage is the second microservice

**Date:** 2026-08-13 · **Status:** accepted

Zivo splits MinIO writes and chunked-upload HTTP out of the product API into a
separately deployed FastAPI service (`storage/`, `storage.zivo.fyi`, Helm chart
`zivo-storage`). Bytes live in the existing MinIO. Object metadata and upload
sessions write `storage.*`. The product API keeps `qb.documents`, intel ingest
rows, and enqueue.

**Why.** Uploads are plumbing, not the question loop. A dedicated process and
write-owner for objects shrinks blast radius of a MinIO/credential bug without
splitting learn/grade/chat. Same bar as [ADR 0007](0007-auth-microservice.md):
process + hostname + exclusive schema writer.

**Data.** Same Postgres, new `storage` schema. Storage Alembic
(`alembic_version_storage`) is the only writer of `storage.*`. Not a second
database. `qb.documents.storage_key` stays the product pointer. Do not retarget
intel FKs.

**Cookie.** Duplicate JWT decode + `auth.account.session_version` read. Shared
`SECRET_KEY` (HS256) this slice because offline packs still HMAC with that key
([ADR 0006](0006-offline-answer-keys.md)). Guest cookie `zivo_demo_id` is
verified, not minted. Product still owns `POST /api/guest` and claim.

**Internal.** Product API and workers call `/api/storage/internal/*` with
`X-Zivo-Internal-Key` equal to `SECRET_KEY`. Browser chunked traffic uses
`/api/storage/chunked`. Complete returns an object id; product
`POST /api/sources/from-object` creates the document.

**JWKS.** Auth + api + storage are three cookie verifiers. JWKS is the slice
*after* storage (and jobs, if jobs stays cluster-internal and does not verify
browser cookies). Do not rotate `SECRET_KEY` here.

**Not in storage.** Import URL / YouTube / paste (they create documents, not
blobs). Document list/get/purge. Ingest/cook. Engines. Guest mint.

**Considered and rejected.**

- *Hostname `files.zivo.fyi`.* Keep `storage` to match `auth` / `api`.
- *JWKS in this slice.* Offline packs still share `SECRET_KEY`.
- *One process per engine.* Contradicts [ADR 0004](0004-swappable-policy-seam.md).
- *Shared Python MinIO package.* Would become a distributed monolith; product
  talks HTTP.
- *Moving `qb.documents` into storage.* That table is the product FK hub.

**Consequence.** DNS needs a `storage.zivo.fyi` A record to the VPS before TLS
will issue. Local Next rewrites `/api/storage` to `:8202`. Product no longer
imports boto3.
