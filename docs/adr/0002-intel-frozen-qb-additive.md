# Freeze the `intel.*` schema; add product tables only in `qb.*`

**Date:** 2026-06-27 · **Status:** accepted

The `intel.*` schema (`backend/schema/intel_foundation.sql`) is treated as frozen
legacy DDL. All Question Better product data goes in additive `qb.*` tables
(`backend/schema/qb_app.sql`, `qb_infra.sql`), applied via Alembic. Product reads/writes
join across both schemas but never alter `intel.*` DDL.

**Why.** `intel_foundation.sql` is a ported, general-purpose knowledge-graph foundation
(entities, assertions, measurements, projections) shared with the zivo lineage.
Keeping it unchanged means upstream improvements stay mergeable and our migrations only
ever *add*. The product maps its nouns onto the existing graph (a question is an
`intel.assertion`, a learner an `intel.entity`, an answer an `intel.measurement`).

**Considered and rejected.** Forking `intel.*` to add product columns directly —
rejected because it would make the foundation un-mergeable and blur which tables are
ours to evolve.

**Consequence.** New product state is sometimes modeled by reusing a generic `intel`
table (e.g. calibration writes to `intel.projection`) rather than a bespoke `qb` table;
that is deliberate. See [docs/DATA_MODEL.md](../DATA_MODEL.md) for the table-by-table map.
