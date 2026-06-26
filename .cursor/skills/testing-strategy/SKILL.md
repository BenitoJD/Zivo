---
name: testing-strategy
description: "Use for anything testing: writing tests, choosing the right test type, deciding what to mock, reviewing coverage, and running the right tests after a change."
---

# Testing Strategy

Use this skill when the task is about test strategy, test selection, test review, or deciding what tests need to be run to trust a change.

Prefer this skill for repo-wide testing decisions. Pair with a stack-specific skill only when implementation details matter.

For backend pytest mechanics such as fixtures, async tests, parametrization, and `unittest.mock` usage, use `python-testing` skill.

## Goals Of Testing

Testing exists to make change cheap and safe.

Use tests to:

- catch regressions before users do
- protect critical business behavior
- make refactors safer
- document expected behavior in executable form
- narrow debugging when failures happen

Do not use tests to:

- prove the code is perfect
- restate the implementation line by line
- test framework or database vendors for doing their jobs
- maximize test counts or coverage for their own sake

Coverage is a signal, not the goal. Favor risk coverage over raw percentages.

## Core Rule

Catch high-risk failures at the lowest reasonable layer.

Pick the cheapest layer that can faithfully reproduce the failure.

- Go lower for speed, precision, and coverage breadth.
- Go higher only when the higher layer adds a new failure class: wiring, contract, persistence, browser behavior, or live integration behavior.

## Test Layers

## Recommended Stack

Use the smallest stable toolset that covers the repo well.

- Backend tests: `pytest`
- Backend mocking: `unittest.mock`
- Frontend unit/component tests: `vitest` + React Testing Library
- Browser E2E tests: `playwright`
- Coverage: `pytest-cov` for backend when coverage measurement is needed

Prefer this stack unless the user explicitly asks for something else.

### Unit Tests

Use for pure logic and small units with clear inputs and outputs:

- calculations
- validation rules
- transformations
- branching logic
- formatting helpers
- retry classification

Write many of these. They are the cheapest, fastest, and easiest to debug.

Do not use unit tests to pin:

- private helper names
- internal call order
- exact implementation structure
- framework internals

Good unit tests protect behavior. Bad unit tests punish harmless refactors.

### Service Tests

Use for orchestration and error handling in application use cases:

- sequencing across dependencies
- retries and fallback
- state transition policy
- side-effect rules
- partial failure handling
- idempotency behavior

A service test asks:

Given these dependency behaviors, does this use case coordinate the workflow correctly?

Service tests may use mocks/fakes or a real DB depending on whether persistence truth is part of the question.

### API Tests

Use for FastAPI HTTP contract behavior:

- request validation
- auth and permissions
- status codes
- response shape
- route wiring

API tests usually keep the app, request stack, and test DB real.

It is often reasonable to mock:

- external AI providers
- email/SMS/payment providers
- background queues
- third-party webhooks

It is usually not reasonable to mock the DB in API tests unless the test is intentionally very narrow and the DB adds no value.

### E2E Tests

Use for critical user journeys and browser-only failures:

- auth redirect loops
- page-level interaction flows
- user-visible state transitions
- client/server wiring issues

Keep these few and focused. They are expensive, slower, and more brittle than lower layers.

## Test Shape

Default shape:

- many unit/service tests
- some API tests
- few E2E tests

Reason:

- lower layers are cheaper and more precise
- middle layers catch contract and persistence gaps
- top layers prove critical user journeys still work

Do not duplicate the same case across unit, service, API, and E2E without a reason. Repeat a case at a higher layer only if that higher layer adds a new failure class.

Use "integration" as a style, not a primary bucket:

- a service test can be isolated or integration-style
- an API test can be isolated or integration-style
- migration tests are always integration-style

Organize tests by the layer under test, not by whether the dependencies are mocked or real.

## What To Mock

Mock things that are outside the question the test is answering.

Usually okay to mock:

- paid or rate-limited external APIs
- nondeterministic third-party systems
- email/SMS/payment delivery
- background queues when queue behavior is not the subject
- clocks, randomness, and network failures when you need deterministic control

Use `unittest.mock` for backend mocks and stubs. Prefer constructor/dependency injection over patching when possible.

Keep real when they are part of the truth being tested:

- DB for repository/query/persistence/migration questions
- auth dependency shape for permission tests
- request/response stack for API contract tests
- browser/app runtime for E2E tests
- external provider itself for live smoke tests

## What Not To Mock

Do not mock:

- the DB in DB integration or migration tests
- the provider in a live integration smoke test
- the route/auth stack in an API auth test
- persistence when the bug is about durable state
- UI runtime when the bug is about a browser flow

Avoid over-mocking in API tests. A mocked DB plus mocked service plus mocked repository usually proves very little.

## When To Write Tests

Write a test when:

- a bug was fixed and should never regress
- business logic has meaningful branching
- auth, permission, money, or destructive actions are involved
- a migration changes schema or data shape
- a query or persistence path is non-trivial
- an external integration contract matters
- a critical user flow must remain stable
- a refactor removes fallbacks, hidden retries, or silent error handling

Every real bug fix should add a regression test or a concrete guardrail.

When auth or permission behavior changes, test each relevant role explicitly. If user and admin behavior differ, cover both instead of assuming one role is representative.

When a refactor removes fallbacks or silent failure behavior, add tests that prove the new contract explicitly: fail fast where appropriate, return the intended error/status, and avoid silently returning `None`, `[]`, `{}`, or degraded success responses unless that behavior is an intentional product requirement.

## When Not To Write A New Test

Do not add a new test just because a file changed.

Skip or avoid new tests when:

- the change is purely mechanical and already covered
- the test would only restate implementation details
- a higher-signal existing test already protects the risk
- the behavior belongs to a vendor/framework, not the app
- the new test would be noisy, brittle, or redundant without adding a new failure class

If a small refactor breaks many tests without changing behavior, the tests are probably too implementation-coupled.

## Persistence Truth

Use a real DB when the correctness question is about what is actually stored or returned from storage.

Examples:

- query filtering, ordering, pagination, joins
- defaults and constraints
- enum/value mapping
- durable state transitions
- migrations and backfills

For these tests, seed real records, run the real repository/service/query path, then assert on the durable result. Reload state from the DB when needed instead of trusting in-memory ORM objects.

Do not write persistence tests just to prove basic inserts work when no real persistence risk exists.

## Service Orchestration

Use service tests for workflows like:

- call provider, persist result, update status
- retry on transient failure, not on permanent failure
- charge credits only on success
- enqueue follow-up work after creation
- handle partial success in bulk workflows
- avoid duplicate side effects on retries

Assert on observable behavior:

- returned value
- translated error
- state transition request
- important dependency interactions
- important side effects not happening on failure

Do not assert private helper order unless the ordering itself is the contract.

## Test Placement

Place tests by the layer under test.

Backend:

- `backend/tests/unit/`
- `backend/tests/services/`
- `backend/tests/api/`
- `backend/tests/migrations/`

Frontend:

- colocated `*.test.ts` or `*.test.tsx` for unit/component tests
- this repo has no `playwright`/E2E suite today (see the `e2e-testing` skill)

External live integration checks:

- `backend/scripts/service_tests/`

Rules:

- service tests with mocked dependencies and service tests with a real DB live together in `backend/tests/services/`
- API tests with mocked providers and API tests with a real DB live together in `backend/tests/api/`
- repository/query tests that are not testing a service should go in `backend/tests/unit/` only if they are pure logic; if they require a real DB, place them with the nearest service or API area, or add a focused module under `backend/tests/services/`
- migration tests belong in `backend/tests/migrations/`
- external paid/rate-limited smoke checks belong in `backend/scripts/service_tests/`, not the normal fast pytest suite

## Shared Test Helpers

Reuse shared setup where it removes repetition without hiding test intent.

Good shared helpers:

- pytest fixtures for app, DB session, API client, auth headers
- factories/builders for common test data
- frontend render helpers such as `renderWithProviders(...)`
- small fake implementations used across multiple tests

Rules:

- share setup, not assertions
- share data builders, not test meaning
- keep helpers small and composable
- avoid helper layers that make the test hard to read
- if repeated setup appears and no shared fixture/helper exists yet, create a reusable one if the abstraction stays simple and improves clarity

If a helper makes it harder to see what the test is proving, it is too abstract.

## Change-Based Validation

Run tests that match the changed risk surface. Do not skip the relevant checks.

### Backend Changes

Run:

- `cd backend && pytest`

Add targeted tests for:

- new business logic
- changed services
- auth or permission behavior
- changed repository/query behavior

### Frontend Changes

Run:

- `cd frontend && npm run lint`
- `cd frontend && npm run build`

Add targeted UI/component tests with `vitest` + React Testing Library where behavior changed. Add `playwright` coverage for important flow changes.

### API Behavior Changes

Run targeted API checks. Prefer real request/response testing.

In this repo, use `scripts/dev.sh curl` for targeted API validation against the current branch instance. Add `--instance <instance>` when needed.

When using local dev auth bypass, ensure `DEV_AUTH_BYPASS=true` in `backend/.env.local` and use the repo-standard headers:

- user flow: `X-Dev-Auth-Uid: sample-user-1`
- admin flow: `X-Dev-Auth-Uid: sample-admin-1` and `X-Dev-Auth-Admin: true`

When auth, permission, or role-scoped behavior changes, include each relevant role path in validation. Do not treat a single-role check as sufficient when different roles can see different results, permissions, or failure modes.

### Migration Changes

Run:

- `cd backend && ./scripts/test_alembic_migrations.sh`

Add migration tests for upgrade/backfill behavior when schema or data shape changed.

### External API Integration Changes

Run the dedicated service test in `backend/scripts/service_tests/` when available.

Also:

- verify model names and params against official docs or client-library code
- use the minimum-cost smoke test when usage is paid or rate-limited
- report concrete run evidence

### UI Flow Changes

Prefer targeted `playwright` coverage with screenshots for changed critical paths.

### Before Handoff

The final evidence should answer:

- what changed and why
- what failure modes were tested
- what commands were run
- what remains risky or unverified

## Review Heuristics

When reviewing a test plan or suite, ask:

- does this test protect behavior or implementation details?
- is this the lowest reasonable layer?
- does this higher-layer test add a new failure class?
- are expensive tests being used only where realism matters?
- is the suite likely to stay stable through healthy refactors?
- did the change add enough evidence for its risk level?

## Bottom Line

Choose the cheapest test that can catch the failure honestly.

- unit tests for logic
- service tests for orchestration
- API tests for HTTP contracts
- integration tests for persistence and live collaboration
- E2E tests for critical user journeys

Use real systems when they are the source of truth. Mock only what is outside the question being tested.
