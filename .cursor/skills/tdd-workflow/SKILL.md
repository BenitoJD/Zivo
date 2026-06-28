---
name: tdd-workflow
description: Use when writing features, fixing bugs, or refactoring in this repo. Test-first for the FastAPI backend; correctness verified by the real checks this repo actually runs.
---

# Test-Driven Development Workflow

Keeps development test-first and reviewable. The canonical testing rules are in
[docs/CONVENTIONS.md → Testing](../../../docs/CONVENTIONS.md#4-testing); this skill is
the *workflow* around them.

## Repo testing surface (what this repo actually runs)

- **Backend:** `pytest`. CI runs the DB-free unit trees `tests/unit/` and
  `tests/unit/`; DB-bound tests live in `tests/integration/` and
  self-skip when Postgres is unreachable. See
  [.github/workflows/ci.yml](../../../.github/workflows/ci.yml).
- **Frontend:** the quality gates are `npm run build` and `npm run lint` in
  `frontend/` — there is **no** frontend unit-test runner configured. Do not invent one
  (no Jest/Vitest/RTL/Playwright unless you are deliberately adding and wiring it).
- Package manager is **`npm`**; the frontend lives in **`frontend/`**.

## Core principles

1. **Tests before code** — write the failing test first when feasible, then the
   minimum code to pass.
2. **Coverage matches risk** — unit tests for isolated logic (DB-free); integration
   tests for DB/API/auth behavior; don't reach for heavy E2E the repo doesn't run.
3. **Verify real behavior** — assert observable behavior, API contracts, permissions,
   and failure modes, not internal implementation details.

## Workflow steps

### 1. State the behavior

```text
When an answer is graded, intel.measurement records exactly one row for it.
```

### 2. List the cases

Happy path · validation failures · auth/permission boundaries · edge cases and
fallbacks · the regression tied to the bug.

### 3. Write the failing test

Pure logic → a DB-free unit test under `tests/unit/`:

```python
def test_difficulty_edge_targets_the_learner_edge() -> None:
    out = choose_next_assertion("difficulty_edge", ["a", "b"], {}, state, {"a": 0.0, "b": 1.5})
    assert out == "b"
```

DB-bound behavior → `tests/integration/`, guarded by the
`_db_reachable()` skip so it never fails on a machine without Postgres
(`tests/integration/test_learn_queue_api.py:19`).

### 4. Run it and confirm red

```bash
cd backend && python -m pytest tests/unit/test_selection.py -k difficulty_edge
```

Do not claim TDD without observing the failing check first.

### 5. Implement the minimum fix

Stay narrow while red: simplest change that satisfies the contract; fix the root
cause, keep it in the right layer (route → service → repository).

### 6. Re-run the targeted test, then adjacent ones

```bash
cd backend && python -m pytest tests/unit/test_selection.py
```

### 7. Refactor without breaking green

Remove duplication, improve names, move logic to the right layer, narrow broad
excepts — behavior unchanged.

### 8. Full relevant verification

```bash
# Backend
cd backend && python -m pytest tests/unit
# Frontend
cd frontend && npm run build && npm run lint
# Schema changes
backend/scripts/test_alembic_migrations.sh
```

## Patterns

### Backend behavior test

```python
def test_grade_records_one_measurement(client, guest_headers) -> None:
    res = client.post("/api/mcq/grade", json={"assertion_id": aid, "choice_index": 0}, headers=guest_headers)
    assert res.status_code == 200
```

### Service test (pure)

```python
def test_elo_update_lifts_ability_on_a_correct_answer() -> None:
    assert elo_update(0.0, 0.0, correct=True).ability > 0.0
```

## Mocking

Mock external boundaries (LLM providers, slow network, object storage), not your own
business logic. Prefer real validation and real serialization unless the test is
specifically about failure injection.

## Common mistakes

- ❌ Testing implementation details (`service._cache_key == ...`) → ✅ assert the
  observable result.
- ❌ Writing code before proving the failure → ✅ red first, then the minimum fix.
- ❌ A frontend-only fix for a backend authorization rule → ✅ enforce and test it on
  the backend.
- ❌ Inventing a frontend test runner → ✅ use `npm run build` + `npm run lint`.

## Success metrics

The behavior is captured by a test; the first relevant test was observed failing
before the fix; targeted tests pass after; the full relevant checks pass; coverage
matches the risk of the change.

---

**Remember:** TDD here is about proving behavior and protecting high-risk paths with
the checks this repo actually runs — not maximizing test count.
