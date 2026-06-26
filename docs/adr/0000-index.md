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

To add one: scan for the highest number, increment, keep it short.
