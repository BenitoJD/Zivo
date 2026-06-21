---
name: refactor
description: Refactor existing backend or frontend code with test-first safety. Follow `agents/coding-guidelines.md`, add missing relevant tests before changing behavior, and apply stack-specific best practices from `fastapi` or `next-best-practices` as appropriate.
---

# Refactor

Use this skill when the task is explicitly to restructure, simplify, or
modernize existing code after the current behavior is understood.

## Required Rules

- Follow the coding guidelines in `agents/coding-guidelines.md`.
- Before refactoring, check whether relevant tests already exist.
- If relevant tests are missing, add them first.
- Run the relevant tests before refactoring to confirm the baseline.
- Refactor only after the baseline is covered.
- Run the same relevant tests again after the refactor to verify behavior
  is preserved.
- Reuse shared test setup where it removes repetition without hiding test
  intent.
- Share setup and small data builders, not assertions or test meaning.
- If repeated setup appears across tests, centralize it only when the
  helper stays small, composable, and easier to read than the inline
  version.

## Stack Guidance

- For FastAPI or backend refactors, also follow the `fastapi` skill for
  framework and Pydantic best practices.
- For Next.js or frontend refactors, also follow the
  `next-best-practices` skill for App Router, RSC, data, and routing
  best practices.

## Operating Flow

1. Read the touched area and define the exact behavior that must stay the
   same.
2. Identify the smallest relevant automated tests for that behavior.
3. If coverage is missing, create the regression tests first.
4. Run the baseline tests and fix the tests or understanding before
   changing production code.
5. Refactor toward your current refactoring goal. If it's not specified, work
   towards simpler structure, clearer names, smaller units, and
   less duplication without widening scope.
6. If the refactor exposes repeated test setup, extract a shared helper
   or fixture only when it improves clarity and does not hide what the
   test is proving.
7. Re-run the relevant tests after the refactor.
8. If the refactor touched shared or risky code paths, run the next
   nearest validation for that area too.

## Validation Expectations

- Backend refactors: prefer targeted `pytest` first, then broader backend
  validation if the change surface is wider.
- Frontend refactors: run the smallest relevant lint, type-check, and
  targeted UI or component validation for the changed area.
- Do not claim a refactor is complete if the changed behavior was not
  covered and re-verified.
