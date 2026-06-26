# Raw parameterized SQL in repositories, not an ORM data layer

**Date:** 2026-06-27 · **Status:** accepted

The data path is hand-written SQL through `sqlalchemy.text()` with bound parameters
(`app/repositories/intel.py:23`), not ORM-mapped queries. A few `app/models/` classes
exist, but reads and writes against `intel.*` / `qb.*` go through explicit SQL.

**Why.** The product is built on top of `intel_foundation.sql`, a pre-existing schema
we do not own and do not change (see [ADR 0002](0002-intel-frozen-qb-additive.md)).
Hand-written SQL keeps us exact about what hits Postgres, lets us use Postgres-specific
features (`jsonb`, `DISTINCT ON`, partial indexes, `pg_notify`) directly, and avoids
maintaining an ORM mapping over tables that change outside our control.

**Considered and rejected.** Full SQLAlchemy ORM models for every table — rejected
because mapping a large frozen schema we don't own is upkeep with no payoff, and it
hides the exact query, which matters on the latency-sensitive read paths.

**Consequence.** SQL is parameterized **always** (`:id`, never f-strings) to keep it
injection-safe; that discipline is a convention, not yet a linter — see
[ADR 0005](0005-enforcement-and-known-divergences.md).
