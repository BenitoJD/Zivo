---
name: task-workflow
description: "End-to-end task executor for this repo. Use when a user asks to implement a plan, bug fix, feature, delivery task, or agent context change with autonomous execution: create a worktree, branch if required, peform the task, validate, and create PR if relevant for task."
---

# Task Workflow

Use this skill for full execution in this repo.

## 1) Intake and ambiguity gate

1. Read the request and define concrete success criteria. At the end of your task, make sure you meet all the success criterias.
2. Classify effort:
   - Small: clear scope, low risk, no product ambiguity. Proceed immediately.
   - Large/ambiguous: missing behavior, acceptance criteria, data constraints, or UI/UX expectation. Ask ask concise clarifying questions early before coding.
   - For agent context changes, use `manage-agent-context` skill.
3. If the user explicitly asks not to ask questions, proceed with best assumptions and list them in the final report.

## 2) Worktree safety

For PR reviews, create a separate worktree and check out the PR feature branch. Whenever you do this, print its path in your response so the user knows where to find it.

```bash
git fetch origin
git worktree add ../<worktree-name> <pr-branch>
cd ../<worktree-name>
```

For code changes, create a new worktree with a fresh feature branch and install required dependencies using setup script. If you're already on the correct worktree/branch for the feature, do nothing.

```bash
git fetch origin main
git worktree add ../<worktree-name> -b agent/<task-slug> origin/main
cd ../<worktree-name>
./scripts/dev.sh setup
```

* Never switch branches or make changes in the main working copy.

## 3) Implement

1. Make minimal, production-ready, root-cause fixes.
2. Keep code modular and avoid unnecessary complexity.
3. **Find the right/simplest/non-hacky way to solve the problem.**
   - Before writing new code, ask: "Could refactoring existing code make this trivial?"
   - Prefer a small refactor that eliminates the need for a workaround over a hacky patch.
   - If the user asks for approach A but approach B is simpler and achieves the same goal, suggest B.
   - Do not add indirection, abstractions, or cleverness unless they clearly pay for themselves.
   - The goal is the simplest code that correctly solves the problem, not the quickest patch.
4. Follow repo conventions from `AGENTS.md` and
   `agents/coding-guidelines.md`:
   - Python: PEP 8, snake_case modules.
   - TypeScript/React: Next.js patterns, PascalCase components, `useX` hooks.
   - Use `npm` for the frontend.
   - Frontend API calls use the helpers in `frontend/lib/`.
   - If generation screen is changed, mirror change on bulk generation screen.
5. If the task changes agent instructions, make the change using
   `manage-agent-context` as the domain-specific instruction workflow
   while still following this execution workflow for branch setup,
   validation, and PR handoff.
6. If modifying external API integration services:
    - Follow `agents/external-api-integration.md`.
   - Add/update and run a dedicated service integration script (prefer
     `backend/scripts/service_tests/`) before finalizing.
   - If no relevant service integration script exists, implement one first,
     then execute it.
7. Use Cursor subagents (Task tool) for bounded exploration, review, or
   test-writing when useful:
   - You own production code, integration, and final decisions.
   - Subagents may accelerate bounded test, fixture, docs, triage, exploration,
     and review work.
   - Do not ask subagents to edit app/runtime production files without
     inspecting their output.
   - When tests need to be added or updated, decide the coverage yourself, then
     delegate test writing to a fresh subagent when that saves time.

## 4) Start local instance

Start zivo detached and inspect it with status/logs (do not use tailing workflows in task docs):
```bash
./scripts/dev.sh app start
./scripts/dev.sh app status
./scripts/dev.sh app logs
```

The landing site is opt-in. Start it with `--with-landing` only when the task changes files under `landing/**`; otherwise use plain `app start`. Landing binds to webapp port + 1, or the next available port:
```bash
./scripts/dev.sh app start --with-landing
```

If a dev CLI command fails because Docker Desktop is not running, start Docker Desktop manually (or Linux Docker Engine on Linux) and rerun the exact same command. Do not add script-level daemon auto-start workarounds unless the task explicitly asks for them.

## 5) Validate

Classify changed files first, then run the minimum relevant validations:

1. UI-only changes (`frontend/**` only):
   - Run frontend checks and targeted Playwright core E2E flow only.
   - Skip backend `pytest` and curl checks.
2. Backend-only changes (`backend/**` only, no frontend):
   - Run backend tests and targeted curl checks.
   - Skip frontend lint/type-check and Playwright unless backend behavior is only observable through UI.
3. Service integration changes (`backend/app/services/*`):
   - Run dedicated service integration script first (minimum-cost smoke input), then backend checks.
4. Migration-related changes:
   - Run alembic migration test command, plus relevant backend checks.
5. Full-stack changes (`backend/**` + `frontend/**`):
   - Run both frontend and backend validations, plus targeted curl and Playwright core E2E for changed flow.

In final report, explicitly list:

- Why each executed test was required.
- Which suites were skipped and why they were not relevant.

### A) Backend checks (if backend changed by test-selection gate)

1. Run backend tests:

```bash
cd backend && pytest
```

2. API validation with repo helper:
   - Use `scripts/dev.sh curl` (add `--instance <instance>` if needed).
   - Include user flow and admin flow when role behavior differs.
   - For dev bypass testing, use headers:
     - User: `X-Dev-Auth-Uid: sample-user-1`
     - Admin: `X-Dev-Auth-Uid: sample-admin-1` and `X-Dev-Auth-Admin: true`
   - Ensure `DEV_AUTH_BYPASS=true` in `backend/.env.local` when using bypass.

### B) Frontend checks (if frontend changed by test-selection gate)

1. Run lint and type checks:

```bash
cd frontend && npm run build && npm run lint
```

2. Validate changed flows with Playwright core E2E:
   - Read `agents/core-e2e-tests.md` for the flow catalog, role/screen matrix, and run commands.
   - Pick the **relevant** core spec for the changed user journey and run that spec (not an unrelated flow).
   - This repo has no Playwright/E2E suite (see the `e2e-testing` skill); verify UI changes with `npm run build && npm run lint` plus a manual browser check.
   - If your change alters steps, selectors, RBAC, or responsive behavior for an existing journey, **update that spec** in the same task/PR.
   - Set `PLAYWRIGHT_BASE_URL` from `./scripts/dev.sh app status` (`frontend_port`).
   - Capture before/after screenshots manually for UI changes when useful.
   - Run the smallest relevant core spec; PR smoke default is `--project=user-desktop --workers=1`.
   - There is no test matrix; the gates are backend `pytest` and frontend `npm run build && npm run lint`.
   - Expand role/screen coverage when RBAC or responsive layout changes.
   - Report exact commands, per-project pass/fail, and artifact paths (`.webm`, `.png` under `output/playwright/`).
   - Copy task-specific screenshots and E2E videos to `output/playwright/<task-slug>/` for PR upload.
   - Capture at least:
     - one baseline/entry state,
     - one changed primary state,
     - one edge/validation/error state (if relevant).

### C) Alembic migration checks (if migration-related changes exist by test-selection gate)

For any migration work (create, correct, or test), use skill: `alembic-migrations`

Required migration validation command:

```bash
backend/scripts/test_alembic_migrations.sh
```

### D) Full-stack behavior checks (when backend + frontend both changed by test-selection gate)

1. Run backend tests and frontend checks.
2. Run targeted curl checks for changed API behavior.
3. Run Playwright core E2E for the relevant core spec covering the full changed flow (write or update the spec first if needed — same rules as section B).

### E) External API integration checks (when service integration code changed by test-selection gate)

1. Run the dedicated service test script with minimal-cost inputs.
2. Record the exact command used, model name, raw success/failure output, and concrete input/output evidence (prompt/images/params + generated files or output text paths).
3. If the call fails due to quota/rate limits, report that explicitly and do not claim runtime validation success.

## 6) Result handling

1. If any check fails:
   - Fix root cause.
   - Re-run only affected checks first, then broader sanity checks.
2. If all checks pass:
   - Summarize changes and evidence.
   - Treat review output as advisory rather than hard checks. Verify each finding
     in the real code path before treating it as merge-blocking.
   - Run the `pr-review` skill on the local diff and relevant validation
     evidence. Use a fresh Task subagent with `pr-review` when the diff is
     large or needs broad code-path tracing.
   - If review finds merge-blocking issues, fix them yourself, then run
     `pr-review` again.
   - Repeat this review-fix loop until review yields `pass` with no
     merge-blocking items.
   - Do not proceed to PR handoff while verified merge-blocking review items remain.

## 7) Verification goals

Before claiming a task is complete, confirm these concrete goals are met:

1. **The problem is actually solved** — the original bug is fixed or the feature works as requested.
2. **The approach is the simplest one** — you did not add unnecessary complexity or workarounds.
3. **Tests pass** — all relevant validations ran and passed.
4. **No regressions** — existing functionality still works (verified by tests or manual check).
5. **Subagent outputs are reviewed** — if subagents wrote tests/docs/support
   files or produced findings, you inspected them and integrated only relevant
   work.
6. **PR review was run** — `pr-review` skill checked the diff with no merge-blocking items.
7. **PR description is complete, if a PR is being created** — follows the `pr-review` skill, includes Self Review evidence.

If any applicable goal is not met, fix it before reporting completion.

## 8) PR handoff, when requested

Skip this section when the user explicitly asks not to create a PR or when the
task scope is local-only. In that case, report PR handoff as not applicable in
the final response.

1. Commit with lightweight conventional commit style (`feat:`, `fix:`, `chore:`).
2. When asked to commit changes and there are unrelated modifications,
   commit only the files related to the current feature or task.
3. When the user says "commit the changes" without further scoping, treat
   it as "commit all changes related to the current feature/task you have
   been working on", not just the latest partial edit.
4. Before PR creation, rebase branch onto latest `origin/main`:

```bash
git fetch origin main
git rebase origin/main
```

5. Push rebased branch:

```bash
git push -u origin agent/<task-slug>
```

6. If branch was already pushed before rebase, push with lease protection:

```bash
git push --force-with-lease
```

7. Open a PR with a Markdown description file:

```bash
gh pr create --title "<pr-title>" --body-file /tmp/pr.md
```

8. PR description is mandatory when creating a PR. Draft it in a `.md` body file and follow
    the `pr-review` skill.
9. For UI changes, always upload screenshots and `.mp4` videos together from
   `output/playwright/<task-slug>/` before PR creation; use the
   `pr-screenshot-workflow` skill.

## 9) Final report format

Always report:

1. Branch name.
2. Assumptions or clarifications requested.
3. Files/components changed.
4. Exact validation commands run.
5. Pass/fail outcomes and any residual risks.
6. PR link (if created).
7. If model credits were changed, include a credit mapping summary by model/resolution.
8. PR review status:
   - Whether review was run on the local diff before commit/PR handoff.
   - Any merge-blocking review items that were fixed before handoff.
9. Verification goals status — which goals are met and which are not.
