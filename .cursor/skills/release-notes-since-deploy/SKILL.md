---
name: release-notes-since-deploy
description: Find the latest backend and frontend production deployments, list new Alembic migration files since backend deploy, and create a release-note Markdown report outside the repo with functional/non-functional changes and PR screenshot links.
---

# Release Notes Since Deploy

Use this skill when asked what changed since the last deployment, what
migrations were added after deployment, or to draft user-facing release notes.

## Output contract

Write the final report as Markdown outside the project worktree, unless the
user gives a path. Default:

```bash
../release-notes-since-last-deploy.md
```

Include:

- Backend last deployment: workflow run, completed time, branch, commit, image
  tag when available.
- Frontend last deployment: workflow run, completed time, branch, commit.
- New Alembic migration files after backend deployment.
- Modified existing migration files after backend deployment, separately.
- Functional summary: one row per functional change with user-facing wording
  and screenshot links for quick review.
- Functional changes: user-visible features, fixes, workflow changes, and UX
  improvements.
- Non-functional changes: infra, CI, internal tooling, docs, refactors, tests,
  agent context, and production-support work.
- Screenshots for functional changes where PR screenshot links exist.
- Missing screenshot notes for functional changes that have no PR screenshot
  evidence.

## Deployment source of truth

Use GitHub Actions as the primary source.

Backend production:

```bash
gh run list --workflow backend-build-prod.yml --limit 20 \
  --json databaseId,createdAt,updatedAt,status,conclusion,event,headBranch,headSha,displayTitle,url
```

Frontend production:

```bash
gh run list --workflow frontend-pipeline.yml --limit 20 \
  --json databaseId,createdAt,updatedAt,status,conclusion,event,headBranch,headSha,displayTitle,url
```

Pick the newest run where `status == completed` and `conclusion == success`.
Record `updatedAt` as deployment completion time and `headSha` as the deployed
source commit.

Backend supporting evidence:

- `backend-build-prod.yml` uses production image tags beginning with `Prod_`.
- For release-triggered deploys, the release tag is the image tag.
- For manual deploys, the workflow computes the next `Prod_*` ECR image tag.
- The workflow may also create a later promotion commit like
  `chore: update prod image tags to Prod_*`; do not treat that promotion commit
  as the deployed application source commit unless the successful deploy run
  used it as `headSha`.

Frontend supporting evidence:

- `frontend-pipeline.yml` force-syncs the `release` branch from the selected
  source branch and triggers Vercel.
- Historical `UI_*` tags may exist, but prefer the newest successful frontend
  workflow run unless the user explicitly asks for tag-based history.

## Baseline and target

Default comparison target is latest `origin/main`.

```bash
git fetch origin main --tags
TARGET_REF=origin/main
BACKEND_DEPLOY_SHA=<from latest backend success run>
FRONTEND_DEPLOY_SHA=<from latest frontend success run>
```

If the user asks for a branch validation instead of release notes, compare that
branch to the deployment refs and say it is branch validation, not deployed
release inventory.

## Migrations

Use the backend deployment SHA as the baseline:

```bash
git diff --name-status "$BACKEND_DEPLOY_SHA..$TARGET_REF" -- backend/alembic/versions
```

Classify:

- `A`: new migration file after deployment.
- `M`: existing migration file changed after deployment.
- `D`/`R`: destructive or rename migration changes; call these out clearly.

Do not call modified migration files "new".

## PR and commit inventory

Use first-parent history to identify merged PRs:

```bash
git log --first-parent --oneline "$BACKEND_DEPLOY_SHA..$TARGET_REF"
git log --first-parent --oneline "$FRONTEND_DEPLOY_SHA..$TARGET_REF"
```

For each PR number in merge commits, inspect the PR:

```bash
gh pr view <number> \
  --json number,title,body,mergedAt,author,url,files,commits,labels
```

Use PR title/body/files/commits to classify the change. Prefer behavior from
code and PR body over commit-title guesswork when they conflict.

Functional examples:

- new user workflows or screens
- visible UI changes
- behavior changes in shoots, bulk shoots, CRM project flows, auth, billing, or
  generation
- user-visible bug fixes or timezone/display fixes
- performance changes that users directly experience

Non-functional examples:

- agent skills, AGENTS.md, and subagent setup
- CI/CD, deployment scripts, drift checks, PR tooling
- infrastructure and production-support docs
- dev CLI, local setup, tests, refactors, dependency cleanup
- backend internals with no user-visible behavior

## Screenshots

Use the existing PR screenshot workflow knowledge and AssetLink links already in
PR bodies.

Search each functional PR body for sections named `## UI Screenshots`,
`## UI screenshots`, or links containing AssetLink paths such as `/uploads/` or
`/assets/`.

Keep screenshot evidence as Markdown links or images:

```markdown
![Feature screenshot](https://...)
```

If the PR has an AssetLink batch gallery link plus direct image or video links,
include the direct media links under the feature and keep the batch gallery as a
source link.

Also add every functional PR with screenshots to the `Functional Summary`
section above the detailed functional changes. Keep the summary compact and
optimized for quick release-review scanning:

- link the PR
- write the user-facing summary in one sentence
- include the AssetLink batch gallery link when available
- include one or two representative direct image or video links when available
- say `missing` when a functional change has no screenshot or video evidence

Current repo helpers upload screenshots and `.mp4` videos to AssetLink:

- `scripts/upload_pr_screenshots.sh`
- `pr-screenshot-workflow` skill

They do not provide a fetch/download API for arbitrary AssetLink resources. If
the user asks for local media files and direct URLs cannot be downloaded, state
that AssetLink needs a read/fetch API and keep the remote links in the report.

## Report shape

Use this structure:

```markdown
# Release Notes Since Last Deployment

Generated: <timestamp>
Target ref: <target ref and sha>

## Deployment Baselines

| Surface | Completed | Source | Commit | Run |
| --- | --- | --- | --- | --- |
| Backend | ... | ... | ... | ... |
| Frontend | ... | ... | ... | ... |

## New Backend Migrations

...

## Functional Summary

| Change | Release-note summary | Screenshots |
| --- | --- | --- |
| [#123 Feature title](...) | One user-facing sentence. | [Gallery](...) · [Image](...) |

## Functional Changes

### <Feature or fix title>

- PR: [#123](...)
- Area: <backend/frontend/full-stack>
- User impact: ...
- Release-note wording: ...
- Screenshots:

![...](...)

## Non-Functional Changes

...

## Missing Evidence

- ...
```

Keep release-note wording user-facing: no commit hashes, implementation details,
or internal tooling names unless the user audience is internal.
