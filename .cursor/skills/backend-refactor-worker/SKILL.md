---
name: backend-refactor-worker
description: Incremental backend refactor worker for citepage FastAPI modernization and behavior-preserving repository extraction.
---

# Backend Refactor Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Use this skill for backend refactor features in citepage that change architecture or code organization while preserving behavior, including:
- app bootstrap/lifespan refactors
- typed settings/config cleanup
- request-scoped DB/session cleanup
- repository extraction for DB interactions
- auth dependency cleanup
- ConfigDict schema migration
- fallback-reduction refactors
- backend test-fixture isolation improvements
- targeted backend-domain rollout of the new patterns

## Required Skills

Workers should invoke these repo skills during implementation when they are available in the worker session:
- `fastapi` — for FastAPI and Pydantic best practices, especially `Annotated`, response modeling, and lifespan/app-factory direction.
- `backend-patterns` — for backend layering, service/repository boundaries, DB access, and error-handling structure.
- `testing-strategy` — to decide the right changed-area tests and validation depth before implementation.
- `python-testing` — when writing or updating backend pytest coverage and fixtures.
- `refactor` — for incremental behavior-preserving refactor discipline.
- `security-review` — whenever the feature touches auth, spoofing, permissions, secrets/config loading, or externally controlled input.

If one or more of these skills are not available through the Skill tool in the worker session, do not block the feature solely for that reason. Instead, read the corresponding repo instructions directly (for example from `AGENTS.md`, `agents/`, and the available platform skill directory) and note the fallback in the handoff.

## Work Procedure

1. Read the assigned feature carefully and identify:
   - the exact files/boundaries in scope,
   - the validation assertions listed in `fulfills`,
   - the highest-risk behavior-preservation points.

2. Read the relevant shared-state files before editing:
   - mission contract and feature description,
   - `.factory/library/architecture.md`,
   - `.factory/library/environment.md`,
   - `.factory/library/user-testing.md`,
   - repo instructions relevant to the touched area.

3. Invoke the relevant repo skills appropriate to the feature before making changes when they are available in the worker session.
   - Prefer `fastapi`, `backend-patterns`, `testing-strategy`, and `refactor` for backend refactor slices.
   - Add `python-testing` when touching tests.
   - Add `security-review` for auth/spoof/config-sensitive slices.
   - If a required repo skill is unavailable through the Skill tool, read its repo-local instruction sources directly and continue, then record that fallback in the handoff.

4. Write tests first where the feature changes behavior risk.
   - Add or update focused pytest coverage for the changed seam before implementation when feasible.
   - For repository extraction or session-boundary refactors, ensure tests prove request-scoped DB injection still works.
   - For fallback removal, add tests that prove the new explicit failure contract or preserved intentional fallback.

5. Implement the slice narrowly.
   - Do not broaden scope beyond the assigned feature.
   - Preserve observable behavior unless the feature explicitly changes it.
   - Keep request/session ownership explicit.
   - When extracting repositories, move DB query/mutation details into repository files and preserve transaction semantics.
   - Avoid hidden global session creation in request/response paths.

6. Run changed-area verification.
   - Always run `cd backend && pytest`.
   - Run targeted regular `curl` checks when API behavior changes.
   - When auth/permission behavior differs by role, validate both user and admin flows using real Firebase-emulator-backed bearer tokens.
   - Determine the backend base URL from the repo-managed stack state when needed.
   - Run limited browser checks only when the feature affects browser-only auth/session/UI state.

7. Inspect results critically.
   - If tests expose pre-existing unrelated failures, document them clearly and return control only if they block safe completion.
   - Do not handwave warnings or regressions introduced in touched code.
   - Clean touched deprecation patterns such as `datetime.utcnow()` or Pydantic `class Config` when the feature scope includes them.

8. Prepare a detailed handoff.
   - Include exact commands, exit codes, concrete observations, test files added/updated, and any discovered issues.
   - If anything remains unfinished, state it explicitly rather than implying completion.

## Example Handoff

```json
{
  "salientSummary": "Refactored backend bootstrap into an app factory with lifespan-managed startup/shutdown, preserved root and ping-db behavior, and added regression tests for Firebase startup failure tolerance and scheduler shutdown. Backend pytest passed and regular curl checks confirmed the local stack still served the expected health responses.",
  "whatWasImplemented": "Created an app-factory-based FastAPI assembly path in backend/app/main.py, moved startup/shutdown responsibilities behind lifespan-compatible helpers, and updated the affected tests to assert root health, ping-db behavior, and non-fatal Firebase init handling without changing the user-visible API responses.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      {
        "command": "cd backend && pytest",
        "exitCode": 0,
        "observation": "Relevant backend regression suite passed after the bootstrap refactor."
      },
      {
        "command": "curl -sf http://127.0.0.1:8004/",
        "exitCode": 0,
        "observation": "Returned HTTP 200 with {\"status\":\"ok\"}."
      },
      {
        "command": "curl -sf http://127.0.0.1:8004/ping-db",
        "exitCode": 0,
        "observation": "Returned HTTP 200 with {\"db\":\"ok\"}."
      }
    ],
    "interactiveChecks": [
      {
        "action": "No browser check required for this slice because the changed behavior was fully backend bootstrap and validated through pytest and curl.",
        "observed": "Skipped appropriately; no browser-only state was affected."
      }
    ]
  },
  "tests": {
    "added": [
      {
        "file": "backend/tests/api/test_system_bootstrap.py",
        "cases": [
          {
            "name": "test_root_route_returns_ok_after_app_factory_refactor",
            "verifies": "Root health route remains available after app bootstrap changes."
          },
          {
            "name": "test_startup_tolerates_firebase_init_failure",
            "verifies": "Firebase initialization failures remain non-fatal during startup."
          }
        ]
      }
    ]
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

Return to orchestrator when:
- the feature requires changing behavior beyond the assigned validation assertions,
- repository extraction reveals ambiguous ownership or transaction semantics spanning multiple future milestones,
- auth/spoof/session behavior cannot be validated safely with the available local setup,
- the changed-area validation requires infrastructure or credentials not currently available,
- pre-existing failures block the slice and cannot be cleanly isolated from the mission scope.
