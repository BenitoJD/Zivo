---
name: production-release-deploy
description: Run the zivo production release workflow: prepare release notes, push to main through the cicd pipeline (tests, GHCR images, production approval gate, ArgoCD promote), monitor the rollout, and hand release notes back to the user.
---

# Production Release Deploy

Use this skill when the user asks to deploy or release zivo production after
checking what changed since the last deployment.

Keep this skill separate from `release-notes-since-deploy`: that skill is the
read-only release inventory/reporting step; this skill is the production deploy
orchestration step.

## Safety

This is production work. Follow `agents/prod-safety.md`.

## Ship gates (required before deploy)

Run CI-parity checks locally before pushing to `main`. See `agents/ship-gates.md`.

```bash
./scripts/ship-gates.sh
```

Or confirm the target SHA already has a **green** `cicd` run. Do not deploy
from a commit that failed lint, tests, or frontend build.

Before any prod command or production deployment trigger:

- Ask for explicit in-thread approval.
- Show exact commands.
- State target, expected impact, and rollback/mitigation.
- Treat approval as single-use for the command batch.

Use two approval phases:

1. Read-only preflight and monitoring approval.
2. State-changing deployment approval for the push to `main` (which starts the
   `cicd` pipeline).

Do not push to `main`, dispatch the `cicd` workflow, or run `kubectl`/`k3s`
against prod without approval.

## High-level flow

1. If not already done, run `release-notes-since-deploy` first and
   produce/update the Markdown report outside the repo. If the user explicitly
   asks to skip release notes, record that skip and continue only after
   confirming deployment approval.
2. Review the release-note summary with the user and ask for explicit approval
   to continue to deployment.
3. Confirm target branch, normally `main`.
4. Push to `main` (or dispatch the `cicd` workflow) and approve the
   `production` environment gate when GitHub pauses the promote job.
5. Watch the workflow until success/failure.
6. Monitor the ArgoCD sync and the cluster until the new images and pods are
   replaced and healthy.
7. Give the user the release notes report path and a concise deployment summary.
   Do not send release notes to users automatically unless explicitly asked.

## Release notes preflight

Use `release-notes-since-deploy` and verify the report has:

- deployment baselines
- new backend migrations
- functional summary
- detailed functional changes
- non-functional changes
- screenshot links

Default report path:

```bash
../release-notes-since-last-deploy.md
```

After the report is ready, summarize the functional summary and ask whether to
continue to deployment. Do not trigger deployment until the user explicitly
approves.

## Deployment

zivo ships API and web from a single pipeline (`.github/workflows/cicd.yml`),
not separate backend/frontend pipelines.

Deploy by pushing to `main`, or dispatch manually (optional semver override):

```bash
gh workflow run cicd --ref main            # add -f version=1.2.4 to pin a semver
```

Monitor the run:

```bash
gh run list --workflow cicd --branch main --limit 5 \
  --json databaseId,status,conclusion,createdAt,updatedAt,headSha,url

gh run watch <run-id> --exit-status
```

Expected pipeline behavior:

- Unit tests (backend + auth/storage/practice/content/study/library/admin) run
  first on the self-hosted `zivo` runner; lint and the frontend build are not
  run in CI.
- `zivo-python-deps` builds first; then `zivo-migrate`, `zivo-auth`,
  `zivo-storage`, `zivo-practice`, `zivo-content`, `zivo-study`, `zivo-library`,
  `zivo-admin`, `zivo-worker`, and `zivo-web` build and push to GHCR. Tags:
  semver on `main` (e.g. `1.2.4`, patch auto-bumped from the latest `v*` git
  tag), `sha-<short sha>` on every other branch.
- GHCR pushes use a GitHub App installation token minted at runtime from the
  `ghcr` environment secrets (`GHCR_APP_ID` / `GHCR_APP_PRIVATE_KEY`); no PAT
  anywhere. The workflow token is used only for repo writes in the gated
  promote job.
- On `main`, the `promote` job pauses on the `production` environment until a
  required reviewer approves; the approval commits the new tag into
  `infra/k8s/environments/prod/*-values.yaml` and ArgoCD rolls out.
- After the promote commit lands, a GitHub release `v<semver>` is created with
  auto-generated notes.
- Alembic migrations are NOT run by the pipeline. When the release includes
  schema changes, run them from a workstation with the admin kubeconfig via
  `scripts/run-k8s-schema-migrate.sh` before approving the gate.
- Product schema uses `ghcr.io/<owner>/zivo-migrate`. Workers use
  `ghcr.io/<owner>/zivo-worker`. There is no `zivo-api` Deployment.

## Prod VPS / Kubernetes monitoring

Prod is an HA K3s cluster (3 masters + 3 workers + 2 HAProxy LBs). From a
workstation:

```bash
export KUBECONFIG=~/.kube/zivo-ha.conf
```

Prod namespace:

```text
zivo
```

Read-only monitoring commands:

```bash
KUBECONFIG=~/.kube/zivo-ha.conf kubectl -n zivo get pods -o wide
KUBECONFIG=~/.kube/zivo-ha.conf kubectl -n zivo get deploy
KUBECONFIG=~/.kube/zivo-ha.conf kubectl -n zivo get ingress
KUBECONFIG=~/.kube/zivo-ha.conf kubectl -n zivo get pods -o jsonpath=\''{range .items[*]}{.metadata.name}{"\t"}{.status.phase}{"\t"}{range .spec.containers[*]}{.image}{" "}{end}{"\n"}{end}'\'''
KUBECONFIG=~/.kube/zivo-ha.conf kubectl -n argocd get applications
```

If the release includes schema changes, inspect the manually run migration job:

```bash
KUBECONFIG=~/.kube/zivo-ha.conf kubectl -n zivo logs job/alembic-migrate
```

For each rollout, verify:

- ArgoCD Applications are `Synced` and `Healthy`.
- Deployments are available.
- The migration Job (when run) completed successfully.
- New auth/storage/practice/content/study/library/admin, web, worker-io, and
  worker-cpu pods are running.
- Running pod images use the new tag (semver on `main`, `sha-<short>` on branches).
- No relevant pods are in `CrashLoopBackOff`, `ImagePullBackOff`, `ErrImagePull`,
  `Pending`, or repeatedly restarting.

Useful waits, still read-only:

```bash
KUBECONFIG=~/.kube/zivo-ha.conf kubectl -n zivo rollout status deployment/zivo-auth --timeout=10m
KUBECONFIG=~/.kube/zivo-ha.conf kubectl -n zivo rollout status deployment/zivo-worker-cpu --timeout=10m
KUBECONFIG=~/.kube/zivo-ha.conf kubectl -n zivo rollout status deployment/zivo-worker-io --timeout=10m
```

If a deployment name differs from the expected name, inspect current
deployments with `kubectl -n zivo get deploy` and use the actual deployment
name. Do not restart, delete, patch, or sync anything without a new
state-changing approval.

## Stop conditions

Stop and report before continuing if:

- release notes cannot be produced
- the `cicd` pipeline fails (tests, image build, or push)
- the `production` gate is rejected or the promote commit fails to push
- the migration Job (when run manually) fails
- any ArgoCD Application stays `OutOfSync`/`Degraded` or any required pod stays
  unhealthy
- running pod images do not match the new image tag
- SSH/kubectl access to the cluster fails

## Final response

Include:

- `cicd` run URL and result
- deployed image tag (semver, or `sha-<short>` for branch builds)
- ArgoCD sync + pod replacement / rollout result
- migration Job result (when run)
- release notes report path
- anything the user must manually send or verify

Do not claim deployment success unless the pipeline succeeded, the promote
commit landed, and pod replacement was verified.
