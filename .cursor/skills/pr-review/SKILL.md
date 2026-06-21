---
name: pr-review
description: Review code changes before commit or PR handoff. Use for local diffs and PRs. Focus on correctness, security, simplicity, and whether the approach is the right way to solve the problem. Output a clear ship/no-ship decision.
---

# Code Review

Review your diff before committing or opening a PR.

## PR Description

### Goals

Write the PR so a reviewer can answer three questions in under a minute:

- what changed and why now
- where the real risk is
- what evidence makes this ready to merge

### Style

- Write naturally. Avoid robotic or overly formal phrasing.
- Keep it skimmable with short sentences, spacing, and bullets.
- Focus on what matters. Cut filler and low-value detail.
- Lead with user or business impact before mechanics.
- Name the 1-3 highest-risk areas instead of every touched file.
- Remove any sentence that only repeats the diff, commit list, or test names.

### Sections

Every PR description has three core sections. Add optional ones only when they add value.

#### Required

- `## Brief` — 1-3 lines: what changed and why. Not implementation trivia.
- `## Self Review` — what was checked, what was found, what's still uncertain.
- `## Tests Run` — exact commands and outcomes for validations you ran.

#### Optional (add only when helpful)

- `## Risks` — concrete failure modes or rollout-sensitive areas. Skip if genuinely low-risk.
- `## Implementation Details` — key architecture or mechanics the reviewer cannot infer from filenames. Skip if the code is self-explanatory.
- `## UI Screenshots` — remote links only; do not commit images to the repo.

#### Skip these

- `## More Details` — if context is needed, put it in Brief.
- `## Review Delta` — if review found something, describe it in Self Review.
- `## Human Attention Needed` — if a human needs to look at something, say so in Self Review under "Reviewer focus" or Risks.
- `## Not Run` — if you didn't run a test, don't list it.
- `## Mermaid Graph` — only if the flow is genuinely complex and cannot be described in two sentences.

### Tiered descriptions

#### `fix` / `trivial`

Single-line CSS, typo fixes, lockfile updates, snapshots, comment-only changes.

**Required:** `## Brief`, `## Self Review` (1-2 bullets), `## Tests Run`

**Skip everything else.**

#### `standard`

Most PRs: new features, bug fixes, refactoring, UI changes, agent instruction updates.

**Required:** `## Brief`, `## Self Review`, `## Tests Run`

**Add if relevant:** `## Risks`, `## Implementation Details`, `## UI Screenshots`

#### `heavy`

High-risk changes that can break production or affect many users.

**Required:** all of the above, fully filled out.

**Triggers:**
- auth, permission, or secret handling changed
- Alembic migration added or modified
- payment or webhook flow touched
- infra, Terraform, or env-var changes
- new external API integration
- > 200 lines of new logic (excluding generated/boilerplate)

### Self Review format

Write 3-5 bullets in this order:

- `Checked:` the 1-3 highest-risk paths or assumptions you reviewed.
- `Found and fixed:` issues caught during self-review; if none, say what was inspected closely enough to support that conclusion.
- `Still unsure:` any real uncertainty, weakness, or follow-up.
- `Reviewer focus:` the one product, UX, data, or rollout question where reviewer judgment still matters most.

Do not write boilerplate like "looks good" or "no issues found" without naming what was actually checked.

### Tests Run

List exact commands and outcomes. Examples:

- `pytest backend/tests/...` — passed
- `pnpm run type-check` — passed
- `scripts/dev.sh curl ...` — 200 OK
- `scripts/check_platform_skill_drift.py --staged` — passed (when agent instructions changed)

### Agent context changes

Changes to `agents/`, `AGENTS.md`, or platform skill directories affect how every future AI agent behaves.

- Single typo or wording fix: `standard` tier
- New skill, changed workflow, or updated safety rules: `standard` tier with full Self Review
- Always run `scripts/check_platform_skill_drift.py --staged` before opening the PR and include the output in `## Tests Run`.

These are rarely `heavy` unless they change production safety or auth rules.

### Additional rules

- PR descriptions must be Markdown and must not be empty.
- When adding feature flags, use verbose, self-describing names.

## When to use

- Before every commit.
- Before every PR handoff.
- When asked "review this" by the user.
- After meaningful fixes to confirm the branch is ready.

## How to review

### 1. Get the diff

```bash
git fetch origin main
git diff origin/main
```

### 2. Review discipline

This skill is read-only. Report findings from the diff and code paths you inspect; do not modify code.

- Read the real code path and adjacent files before reporting a finding.
- Read dependency docs, source, or types when a finding depends on external behavior.
- Do not report unrealistic edge cases, speculative risks, or broad rewrites unless they clearly address a real bug class.
- Prefer concrete, smallest-fix suggestions over large refactors.

### 3. Check in this order

Stop at the first blocker that would change your decision from `pass` to something else.

1. **Security/auth/permissions** — secrets exposed? Auth bypass? Permission checks missing?
2. **Correctness/data integrity** — wrong logic? Race conditions? Data loss risk?
3. **API contract** — breaking changes without client updates? Response shape changed?
4. **Simplicity** — Is this the **right/simplest/non-hacky** way to solve the problem?
   - Could a simpler approach achieve the same result?
   - Is there unnecessary complexity, indirection, or cleverness?
   - Does it introduce technical debt that will bite us later?
   - Would refactoring an existing module make this change trivial instead of adding a workaround?
   - **If yes: suggest the simpler approach, even if it means taking a step back to refactor.**
5. **Production blast radius** — infra changes? Env vars? Payments? Notifications?
6. **Rollback clarity** — can this be reverted safely? Feature flagged?
7. **Migration/data-model** — destructive migration? Missing downgrade? Locking risk?
8. **Tests** — high-impact paths covered? Test signal exists?

### 4. Decision

Start every review output with one of:

- `pass` — no meaningful risk. Clean, simple, correct.
- `changes-required` — one or more real issues must be fixed before merge.
- `delegate` — needs specialist eyes (security, payments, data model).

Escalate to `changes-required` when:
- Security/auth risk
- Correctness bug
- API breakage without coordination
- Destructive migration with unclear rollback
- No tests for high-impact paths
- **Hacky or overly complex solution when a simpler one exists**

## Output format

```markdown
## Decision: <pass|changes-required|delegate>

### What changed
- <1-2 lines on business/user impact>

### What was checked
- <highest-risk area 1>
- <highest-risk area 2>
- <simplicity check: is this the right approach?>

### Findings
- <item 1: severity + why it matters + suggested fix>
- <item 2: severity + why it matters + suggested fix>

### Suggestions (optional)
- <follow-up 1: impact if addressed>
- <follow-up 2: impact if addressed>
```

Label every finding:
- `merge-blocking` — must fix before handoff
- `optional` — can defer

## Review workflow

### Default operating model

- Run validations for the changed area first (tests, lint, type-check, migration check).
- Then run `pr-review` on the local diff.
- Treat the review as a pre-commit / pre-PR gate, not a post-hoc summary.
- Write the findings into `## Self Review` in the PR description.

### How to handle results

- Read the `Decision` line first. It tells you whether the branch is ready to ship.
- Then read `Findings` and `Suggestions`.
- Treat findings labeled `merge-blocking` as required before handoff.
- Treat findings or suggestions labeled `optional` as non-blocking follow-ups.
- Fix all merge-blocking items before handoff.
- Re-run the impacted validations after fixes.
- Re-run `pr-review` after meaningful fixes to confirm the branch is ready.
- Do not prepare commit or PR handoff while merge-blocking review items remain unresolved.

### Human review required

A human review is required before merge if any of the following are true:

- auth, permission, or secret handling changed
- Alembic migration added or modified
- payment or webhook flow touched
- infra, Terraform, or env-var changes
- new external API integration
- > 200 lines of new logic (excluding generated/boilerplate code)
- changes to agent instructions (`agents/`, platform skills, `AGENTS.md`)

Everything else may be AI-reviewed and merged if the agent review yields `pass` with no merge-blocking items.

### Review environment safety

- Before any review, run `git fetch origin main` to ensure the local view of `origin/main` is current.
- When diffing against `main`, always use `origin/main` as the base — never assume the local `main` branch is up to date.

### Local diff review default

For repo work without an existing PR:

- review `origin/main...HEAD` by default
- use another base/head only when the caller explicitly requests it

### Output expectations

- Start with the `Decision` line.
- Keep findings practical and branch-actionable.
- Focus on correctness, security, rollout risk, regressions, and API or contract breakage.
- Do not turn the review into a style pass unless style is hiding a real risk.
- Explicitly label every finding as `merge-blocking` or `optional`.

## Rules
- Focus on what would realistically change before handoff.
- The simplicity check is not optional. Every review must ask: "Is there a simpler way?"
- If the answer involves refactoring existing code instead of adding a workaround, say so.
