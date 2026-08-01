## Goal
Fix the prod "Database unavailable" 503s. Two independent root causes, both currently masked by the generic `SQLAlchemyError` handler in `main.py:67-70`.

## Fix 1 — `AmbiguousParameter` on document open (primary, 6×)
**File:** `backend/app/services/owner_scope.py` (`owner_scope_sql()`)

**Root cause:** The SQL always binds both `:uid` and `:gid`, but `note_owner_scope` always returns one of them as `None`. psycopg3 sends `None` as an untyped NULL; `(:uid IS NOT NULL …)` gives Postgres no type to infer, so it rejects with "could not determine data type of parameter $3".

**Fix:** Give the params explicit types so NULLs are typed. Replace the two `:uid`/`:gid` comparisons with casts on the equality side, keeping the same logic and same param names (no caller changes):

```sql
document_id = :d AND (
  (account_id IS NOT DISTINCT FROM CAST(:uid AS uuid))
  OR
  (account_id IS NULL AND guest_id IS NOT DISTINCT FROM CAST(:gid AS text))
)
```

`IS NOT DISTINCT FROM` correctly matches NULL account_id for the guest branch and is NULL-safe for the equality, so behavior is preserved for: logged-in user, guest, and legacy `account_id IS NULL AND guest_id IS NULL` rows. Both params are now type-annotated (`uuid` / `text`) → no more ambiguous param.

No changes needed in `saved_notes.py` / `brainstorm.py` — they pass `{d, uid, gid, ...}` unchanged.

## Fix 2 — `intel.measurement is immutable` on guest signup (1×)
**File:** `backend/app/services/guest.py` (`_claim_guest_measurements`, lines 185-211)

**Root cause:** Code does `UPDATE intel.measurement SET subject_entity_id = …` then `DELETE` to re-attribute guest practice to the new account. The `measurement_immutable` trigger (`intel_foundation.sql:848`) blocks UPDATE/DELETE on that table by design (measurements are append-only evidence).

**Fix:** Since the table is INSERT-only, claim by **inserting new rows** for the account entity (mirroring how `answer_signal.py:133-155` already inserts measurements), then leave the immutable guest rows as-is. Concretely:
- Read the guest's measurement rows (`SELECT … FROM intel.measurement WHERE subject_entity_id = :guest_entity`).
- `INSERT … SELECT` copies them with `subject_entity_id = :account_entity`, with `ON CONFLICT DO NOTHING` keyed on `(subject_entity_id, source_assertion_id, metric_concept_id)` (the same uniqueness shape `answer_signal.py:142-144` uses) to dedupe against any the account already has.
- Drop the blocked `UPDATE`/`DELETE`. The orphaned guest rows are harmless (guest entity becomes unused); immutability is respected.
- Return the count of newly inserted rows.

This keeps the public contract (`measurements_moved` count, idempotency on repeated signups, no FK violation) while respecting the schema constraint that the current code violates.

## Why not relax the trigger
The immutability guard is a documented invariant (`intel_foundation.sql:812-854`, AGENTS.md "Legacy intel schema … unchanged"). Reattribute-by-insert is the correct pattern and is already used elsewhere for writes to this table. Loosening the trigger would violate ADR 0002 (intel frozen, qb additive).

## Tests (the reason these shipped)
Existing tests only cover DB-free / skip-without-cookie paths — neither SQL path is hit against a real DB. Add:
- `backend/tests/unit/test_owner_scope.py` — assert the SQL + params dict is accepted against an in-memory or live Postgres (whichever the suite uses) for all three cases (user set, guest set, both None). If the suite is SQLite-only, add a pure-SQL shape test that asserts casts/`IS NOT DISTINCT FROM` are present (regression guard).
- Extend `backend/tests/unit/test_guest.py` with an integration-style test asserting `_claim_guest_measurements` succeeds against the real schema and is idempotent, mirroring how `answer_signal` tests insert measurements.

## Ship gates
- `./scripts/ship-gates.sh`
- `cd backend && ./scripts/test_alembic_migrations.sh` (no migration added, but confirms no drift)
- Manual prod verification after deploy: `kubectl logs -n zivo deploy/zivo-api` shows no new `AmbiguousParameter` or `measurement is immutable`; opening a document + a guest-who-practiced signup succeed.

## Out of scope (flagging, not doing)
- Improving the generic `database_error_handler` to surface the real error class (e.g. 503 for `OperationalError`, 500/409 otherwise). Worth a follow-up — it's why these two unrelated bugs shared one message — but changing prod error semantics is a separate decision.