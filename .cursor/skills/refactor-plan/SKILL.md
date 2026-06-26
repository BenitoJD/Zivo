---
name: refactor-plan
description: Produce a structured refactor plan before code changes. Use when the user asks for a refactor plan.
---

# Refactor Plan

Use this skill when the user wants a refactor plan before implementation.

Do not change production code until the user approves the plan. After approval,
follow the `refactor` skill for test-first implementation.

## Collaboration focus

Cover these areas in every plan:

1. **Current system** — How it works today; key invariants and edge cases;
   targeted code citations only (entry points, core logic, persistence/external
   IO). Use this repo's domain terms (assertion = question, entity/account = learner, measurement = answer).
2. **Behavior contract** — What must stay the same: APIs, DB writes, errors,
   side effects, auth, timing, idempotency.
3. **Scope** — Explicit non-goals so "cleanup" does not become a rewrite.
4. **Risk map** — Callers, migrations, caches, webhooks, jobs, feature flags,
   admin-only paths.
5. **Proposed design** — New boundaries, key decisions, alternatives considered,
   new/renamed modules (names only, not full implementation).
6. **Validation** — Tests to add or run first; commands; manual checks if any.
7. **Open questions** — Numbered list with a recommended default for each.

For architecture-sized refactors, also include: layer diagram (who may import
whom), ownership boundaries, and whether an ADR is warranted.

Do not discover the design and refactor in the same step.

## Plan output template

Deliver the plan using this structure:

```markdown
## Refactor plan

### 1. Problem & success
- What is wrong / painful today?
- What does "done" look like?
- Non-goals

### 2. Current state (as-is)
- 5–10 bullet summary of how it works today
- Key invariants and edge cases
- Call graph or data flow (diagram if >3 hops)
- Targeted code citations (entry point, core logic, persistence/external IO)
- Known smells / tech debt (label fact vs opinion)

### 3. Constraints
- Must-not-change behavior list
- Performance, security, multi-tenant, prod-safety constraints
- Tests that exist vs tests needed before touching code

### 4. Proposed design (to-be)
- New module boundaries and responsibilities
- Key decisions (with alternatives considered)
- New/renamed types, functions, routes — names only
- Migration strategy (big-bang vs phased; feature flag if needed)

### 5. Validation
- Tests to add first
- Commands to run after each phase
- Manual checks if any

### 6. Open questions for the user
- Numbered list; agent recommends a default for each
```

Keep code citations small and purposeful — only lines that prove behavior or
boundaries.

## Handoff to implementation

After plan approval, follow the `refactor` skill.
