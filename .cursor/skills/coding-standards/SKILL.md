---
name: coding-standards
description: >-
  Zivo coding standards. This skill is a pointer: the canonical, code-true rules
  live in docs/CONVENTIONS.md. Use when starting a module, reviewing for quality,
  refactoring to house style, or onboarding to conventions.
---

# Coding Standards — see `docs/CONVENTIONS.md`

The single source of truth for how this repo is built is **[`docs/CONVENTIONS.md`](../../../docs/CONVENTIONS.md)**.
It is descriptive (every rule carries a `file:line` proof) and covers backend layering
(route → service → repository), raw-SQL repositories, the ETA worker pattern, the
swappable-policy seam, error handling, typing, naming, Alembic, and testing.

Design decisions and their rationale are in **[`docs/adr/`](../../../docs/adr/0000-index.md)**.
UI rules (Mantine-only, Calm Paper tokens) are in **[`AGENTS.md → Frontend UI`](../../../AGENTS.md#frontend-ui)**.

Do not restate rules here — that creates a second copy that drifts. If you find a rule
worth recording, add it to `docs/CONVENTIONS.md` (with a proof) in the same PR.

## Repo facts (so this skill never misleads)

- Backend is FastAPI in **`backend/`**; frontend is Next.js in **`frontend/`**.
- Package manager is **`npm`** (lockfile `frontend/package-lock.json`); the frontend
  directory is **`frontend/`**.
- Frontend API calls and shared client logic live in `frontend/lib/`.
- This is the **Zivo / Question Better** codebase — its domain nouns are
  `assertion` (question), `entity`/`account` (learner), `measurement` (answer). Do not
  import another project's vocabulary.

## Prefer the specific skill

- `backend-patterns` / `fastapi` — FastAPI service and router design
- `frontend-patterns` / `design-system` — React, Next.js, Mantine, Calm Paper
- `tdd-workflow` / `testing-strategy` — test-first change execution
- `alembic-migrations` — schema changes
- `security-review` — auth, secrets, uploads
