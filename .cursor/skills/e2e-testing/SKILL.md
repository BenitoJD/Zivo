---
name: e2e-testing
description: >-
  End-to-end testing for this repo. Note: Zivo has no browser E2E suite today.
  Use for how to verify UI/flow changes with the checks the repo actually runs.
---

# E2E Testing — not configured in this repo (yet)

**This repo does not have a Playwright/browser E2E suite.** There is no
`playwright.config.*`, no `e2e/` directory, and no e2e job in CI. Do not assume one
exists, and do not author `*.spec.ts` Playwright tests against imagined infrastructure.

## How to verify a change here

- **Backend behavior:** `pytest` — DB-free unit tests (`tests/unit/`,
  `tests_citepage/unit/`, run in CI) and DB-gated integration tests
  (`tests_citepage/integration/`, self-skipping). See
  [docs/CONVENTIONS.md → Testing](../../../docs/CONVENTIONS.md#4-testing).
- **Frontend build/lint:** `cd frontend && npm run build && npm run lint`.
- **Manual UI check:** run the stack (`./scripts/dev.sh start`) and exercise the flow
  in the browser at `http://localhost:3000`.

## If you are deliberately adding E2E

Adding Playwright is a real decision — record it as an ADR
([docs/adr/](../../../docs/adr/0000-index.md)) and wire the config, a CI job, and the
npm scripts in the same PR, then update this skill and
[docs/CONVENTIONS.md](../../../docs/CONVENTIONS.md). Package manager is **`npm`**;
frontend is **`frontend/`**.
