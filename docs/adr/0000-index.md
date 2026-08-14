# Architecture Decision Records

One dated file per design decision: `NNNN-slug.md`, sequential. Record a decision
when it is **hard to reverse**, **surprising without context**, and **the result of a
real trade-off** — otherwise skip it (full criteria and template:
[.cursor/skills/grill-with-docs/ADR-FORMAT.md](../../.cursor/skills/grill-with-docs/ADR-FORMAT.md)).

Coding rules live in [docs/CONVENTIONS.md](../CONVENTIONS.md); ADRs explain the *why*
behind the shape those rules describe.

| ADR | Decision | Date |
|-----|----------|------|
| [0001](0001-raw-sql-repositories.md) | Raw parameterized SQL in repositories, not an ORM data layer | 2026-06-27 |
| [0002](0002-intel-frozen-qb-additive.md) | Freeze the `intel.*` schema; add product tables only in `qb.*` | 2026-06-27 |
| [0003](0003-mantine-only-calm-paper.md) | Mantine-only UI on the Calm Paper token system | 2026-06-27 |
| [0004](0004-swappable-policy-seam.md) | Era-guess algorithms sit behind a swappable policy seam | 2026-06-27 |
| [0005](0005-enforcement-and-known-divergences.md) | Document what-is; track divergences as dated debt to enforce | 2026-06-27 |
| [0006](0006-offline-answer-keys.md) | Offline Mode ships answer keys to the device in signed, expiring packs | 2026-08-01 |
| [0007](0007-auth-microservice.md) | Identity is the first microservice; shared Postgres; product stub table | 2026-08-13 |
| [0008](0008-storage-microservice.md) | Object storage is the second microservice; MinIO writes leave the product API | 2026-08-13 |
| [0009](0009-jobs-workers-are-the-process.md) | ETA workers are the jobs microservice; do not add a second qb.jobs writer | 2026-08-13 |
| [0010](0010-practice-microservice.md) | Practice banks HTTP leave the product API (`practice/`, `:8203`) | 2026-08-13 |
| [0011](0011-content-microservice.md) | SEO /learn + newspaper admin HTTP leave the product API (`content/`, `:8204`) | 2026-08-13 |
| [0012](0012-study-microservice.md) | Learn/grade/chat HTTP leave the product API (`study/`, `:8205`) | 2026-08-13 |
| [0013](0013-library-microservice.md) | Sources/documents HTTP leave the product API (`library/`, `:8206`) | 2026-08-13 |
| [0014](0014-admin-microservice.md) | Models/debug HTTP leave the product API (`admin/`, `:8207`) | 2026-08-13 |
| [0015](0015-slim-process-images.md) | Each process image contains only that process's code | 2026-08-13 |
| [0016](0016-owned-http-packages.md) | Route modules live with their HTTP process; scheduler lives with IO workers | 2026-08-14 |

To add one: scan for the highest number, increment, keep it short.
