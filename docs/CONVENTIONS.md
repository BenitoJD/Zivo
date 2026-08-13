# Conventions — how this repo is built

> The single source of truth for coding practices in Zivo. If you read one doc
> before shipping a change, read this one. [AGENTS.md](../AGENTS.md) is the index;
> this file holds the coding rules; [docs/adr/](adr/) holds the *why* behind design
> decisions; the UI rules live in [AGENTS.md → Frontend UI](../AGENTS.md#frontend-ui).

**The law of this document**

1. **Descriptive first.** Every rule here describes what the *best existing code
   already does* — then we enforce it. We never write fiction. If the code and this
   doc disagree, one of them is a bug; fix it in the same PR.
2. **Every rule has a reason and a proof.** A rule carries either a tool that
   enforces it or a `file:line` reference showing it is real. A rule that is neither
   enforceable nor referenceable does not belong here.
3. **One source of truth.** Nothing forks these rules. Skills, READMEs, and ADRs
   *link* here; they never restate a rule in a way that can drift.

---

## 1. Repository shape

The monorepo layout and the local-dev commands are in
[AGENTS.md → Layout](../AGENTS.md#layout) and
[AGENTS.md → Local development](../AGENTS.md#local-development). Do not duplicate them.

The one thing to internalize: **product API is `backend/`, identity is `auth/`,
object storage is `storage/`, practice/content/study are HTTP slices that import
backend engines as libraries, frontend is `frontend/`, and the package manager is
`npm`** (proof:
[frontend/package-lock.json](../frontend/package-lock.json),
CI `npm ci` ([.github/workflows/ci.yml](../.github/workflows/ci.yml)),
auth, storage, practice, content, and study import checks in the same workflow). If a skill or
doc names a different frontend directory or a different package manager, it was ported
from another project and is stale; fix it to match the proof above.

## 2. Backend (FastAPI + SQLAlchemy + raw SQL)

### 2.1 Three layers: route → service → repository

- **Rule.** HTTP routes (`app/api/`) stay thin: validate input, resolve auth/access,
  call a service, shape the response. Business logic lives in `app/services/`.
  Persistence and raw SQL live in `app/repositories/` and the services that own a
  table.
- **Why.** Thin routes keep request handling testable and let the same logic run from
  a worker as from an endpoint.
- **Proof.** `app/api/mcq.py:76` (`grade`) validates + delegates to
  `grade_mcq` and `app/services/question_pool.py`; `app/repositories/intel.py:23`
  holds the raw SQL it stands on.

### 2.2 Repositories use parameterized raw SQL via `text()`

- **Rule.** Database access is hand-written SQL through `sqlalchemy.text()` with bound
  parameters, never string interpolation. ORM models (`app/models/`) exist for a few
  entities but the data path is raw SQL against the `intel.*` / `qb.*` schemas.
- **Why.** The product sits on a pre-existing, frozen `intel` schema; raw SQL keeps us
  honest about exactly what hits Postgres and avoids ORM mapping over tables we do not
  own. See [ADR 0001](adr/0001-raw-sql-repositories.md).
- **Proof.** `app/repositories/intel.py:23`; every query binds params (`:uri`, `:id`).

### 2.3 Config is a single typed `Settings`

- **Rule.** All configuration is fields on the pydantic `Settings` object; read it via
  the cached `get_settings()`. New feature flags are added as fields, defaulting to the
  current production behavior (usually `False`), with a one-line comment saying what
  they gate.
- **Why.** One typed, validated source of config; flags default-off so a merge never
  changes production behavior until switched on deliberately.
- **Proof.** `app/config.py:13` (`class Settings`), `app/config.py:130`
  (`get_settings`); the open-world flags at `app/config.py:58-67` model the pattern.

### 2.4 Swappable policy behind a stable seam

- **Rule.** When an algorithm is an era-guess (a metric, a ranking, a selection
  policy), put it behind a named function seam so it can be replaced without touching
  callers. The seam degrades safely to legacy behavior.
- **Why.** Keeps the long-lived loop stable while the metric inside it evolves. See
  [ADR 0004](adr/0004-swappable-policy-seam.md).
- **Proof.** `app/services/adaptive_selection.py` (`select_next` / `choose_next_assertion`)
  dispatches by policy name and falls back to sequence order; the caller
  `app/services/question_pool.py` (`select_next_assertion`) is policy-agnostic.
  Design: [ADAPTIVE_SELECTION_ENGINE.md](ADAPTIVE_SELECTION_ENGINE.md).
  Compat re-exports: `app/services/selection.py`.

### 2.5 Background work goes through the ETA job system

- **Rule.** Anything slow or off the request path (generation, embedding, triage) is
  an enqueued job in `app/eta/`, not inline work in a route. Request handlers
  *enqueue*; workers *execute*. The worker Helm releases are the jobs process
  ([ADR 0009](adr/0009-jobs-workers-are-the-process.md)); do not add a second
  FastAPI that also writes `qb.jobs`.
- **Why.** Keeps request latency bounded and gives crash-safe, reclaimable jobs.
- **Proof.** Worker reserve/reclaim/mark loop at `app/eta/worker.py:55-127`; enqueue
  helpers in `app/services/jobs.py` used from `app/services/question_pool.py`.

### 2.6 Error handling: narrow on the core path, broad only for best-effort

- **Rule.** Catch the specific exception you expect on the core path and let the rest
  surface. A broad `except Exception` is allowed **only** around a best-effort side
  effect whose failure must not break the caller (telemetry, cache writes,
  `pg_notify`, optional refresh) — and it must be commented as such.
- **Why.** Broad excepts on the core path hide real bugs; on a best-effort side effect
  they are the correct way to make the effect optional.
- **Proof of the *good* pattern.** `app/services/document_learn_state.py:39-45` wraps
  only the `pg_notify` notification.
- **Status.** ~54 broad `except` blocks exist in `backend/app`; not all are
  best-effort. Narrowing the core-path ones is tracked in
  [ADR 0005](adr/0005-enforcement-and-known-divergences.md) — do not add new broad excepts on the core
  path.

### 2.7 Typing and naming

- **Rule.** Every module starts with `from __future__ import annotations`. Public
  functions are fully type-hinted. Modules, functions, and variables are
  `snake_case`; classes are `PascalCase`; constants are `UPPER_SNAKE`. Names say what
  a thing *is* in *this* domain (a question is an `assertion`, a learner is an
  `entity`/`account`) — never another project's nouns.
- **Why.** Consistent, self-documenting code; domain-true names stop cross-project
  contamination.
- **Proof.** `app/services/adaptive_selection.py`, `app/services/calibration.py`,
  `app/services/question_pool.py` (constants).

### 2.8 Schema changes go through Alembic

- **Rule.** Product DDL lives in `backend/schema/*.sql`; **Alembic** applies it.
  A product schema change is a new numbered revision in `backend/alembic/versions/`,
  verified by `backend/scripts/test_alembic_migrations.sh`. `intel.*` DDL is frozen;
  product tables are additive in `qb.*`. See [ADR 0002](adr/0002-intel-frozen-qb-additive.md).
  Identity DDL lives in `auth/schema/auth.sql` and is applied by a **separate**
  Alembic chain (`auth/alembic/`, version table `alembic_version_auth`) that only
  touches `auth.*`. Object metadata lives in `storage/schema/storage.sql` and is
  applied by `storage/alembic/` (`alembic_version_storage`) that only touches
  `storage.*`. Credentials and MinIO writes are not performed from the product API.
  See [ADR 0007](adr/0007-auth-microservice.md) and
  [ADR 0008](adr/0008-storage-microservice.md).
- **Why.** Reproducible, ordered, CI-verified migrations; an untouched legacy schema;
  identity and object-storage write-ownership stay in their own services.
- **Proof.** `backend/alembic/versions/001_intel_foundation.py` …
  `045_auth_account_split.py`; `auth/alembic/versions/001_auth_schema.py`;
  `storage/alembic/versions/001_storage_schema.py`;
  CI steps "Alembic migrations", auth "Apply schema", and storage "Apply schema" in
  [.github/workflows/ci.yml](../.github/workflows/ci.yml).

## 3. Frontend (Next.js App Router + Mantine)

The frontend rules — **Mantine-only, no custom components**, where UI lives, allowed
imports, and the full **Calm Paper** token system (typography, color, shadows, radii,
motion) — are documented once in
[AGENTS.md → Frontend UI](../AGENTS.md#frontend-ui). That section is the source of
truth for UI; this file does not restate it.

- **Proof the tokens are real.** `frontend/app/providers.tsx:132` (`createTheme`),
  `:169` (`shadows`), `:179`/`:222` (`shadow: "paper"`). Decision recorded in
  [ADR 0003](adr/0003-mantine-only-calm-paper.md).
- **Enforced by.** `npm run build` in CI; `next` core-web-vitals lint
  ([frontend/eslint.config.mjs](../frontend/eslint.config.mjs)).

## 4. Testing

- **Rule.** Product tests live under `backend/tests/`: `unit/` (DB-free, CI-gated),
  `integration/` (DB-gated, self-skipping), and `services/`. Identity tests live
  under `auth/tests/unit/`. Pure logic goes in `unit/` and must run without a
  database; tests that need Postgres go in `integration/` (or skip when the table
  is missing) and self-skip when the dev DB is unreachable.
- **Why.** Fast, DB-free unit tests gate every PR; DB-bound tests stay opt-in.
- **Proof.** CI "Unit tests" steps run `backend/tests/unit/`, `auth/tests/unit/`,
  `storage/tests/unit/`, `practice/tests/unit/`, `content/tests/unit/`, and
  `study/tests/unit/`
  ([.github/workflows/ci.yml](../.github/workflows/ci.yml)); the skip pattern is in
  `tests/integration/test_learn_queue_api.py` and
  `auth/tests/unit/test_auth_session.py` (`pytest.skip` when `auth.account` is missing).

## 5. What is enforced, and what is not (yet)

| Rule area | Enforcement today | Gap |
|-----------|-------------------|-----|
| Import health, migrations, unit tests | CI (`ci.yml` backend + auth + storage + practice + content + study) | — |
| Dead code, unused imports, undefined names | **`ruff check` (E9, F) in CI** ([backend/ruff.toml](../backend/ruff.toml)) | ruleset is conservative |
| Frontend build + lint | CI (`npm run build`, `npm run lint`) | warnings only in eslint today |
| Python format + broader lint (`I`/`B`) | none | `ruff format`, import sorting not on yet |
| Python types | none | no `mypy` config yet |
| Module size, broad excepts | none | manual review |

Closing the remaining gaps (expand `ruff`, add `mypy`, put `npm run lint` in CI) is the
un-driftability work tracked in
[ADR 0005](adr/0005-enforcement-and-known-divergences.md). Until a rule above is
machine-enforced, its `file:line` proof is what keeps it honest — **a reviewer may
reject a change that contradicts a proof in this doc.**

## 6. When you add or change a pattern

Update this doc in the **same PR**, and add a dated ADR if the change is a design
decision (hard to reverse, surprising without context, a real trade-off — the test in
[docs/adr/ADR-FORMAT](adr/0000-index.md)). The doc is descriptive: if the code moved,
the doc moves with it.
