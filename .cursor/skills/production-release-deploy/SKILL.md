---
name: production-release-deploy
description: Run the zivo production release workflow: prepare release notes, trigger the GitHub Actions deploy with approval gates, monitor the VPS/K3s rollout, and hand release notes back to the user.
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

Run CI-parity checks locally before dispatching deploy. See `agents/ship-gates.md`.

```bash
./scripts/ship-gates.sh
```

Or confirm the target SHA already has a **green** CI run on `main`. Do not deploy
from a commit that failed lint, tests, or frontend build.

Before any prod command or production deployment trigger:

- Ask for explicit in-thread approval.
- Show exact commands.
- State target, expected impact, and rollback/mitigation.
- Treat approval as single-use for the command batch.

Use two approval phases:

1. Read-only preflight and monitoring approval.
2. State-changing deployment approval for the workflow dispatch.

Do not trigger the deploy workflow, SSH into prod, or run `kubectl`/`k3s`
against prod without approval.

## High-level flow

1. If not already done, run `release-notes-since-deploy` first and
   produce/update the Markdown report outside the repo. If the user explicitly
   asks to skip release notes, record that skip and continue only after
   confirming deployment approval.
2. Review the release-note summary with the user and ask for explicit approval
   to continue to deployment.
3. Confirm target branch, normally `main`.
4. Trigger the zivo deploy workflow via GitHub Actions.
5. Watch the workflow until success/failure.
6. Monitor the prod VPS/K3s rollout until the new images and pods are replaced
   and healthy.
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

zivo ships API and web from a single deploy workflow
(`workflow_dispatch`), not separate backend/frontend pipelines.

Production deploy workflow:

```bash
gh workflow run deploy.yml -f branch=main
```

Monitor the run:

```bash
gh run list --workflow deploy.yml --branch main --limit 5 \
  --json databaseId,status,conclusion,createdAt,updatedAt,headSha,url

gh run watch <run-id> --exit-status
```

Expected deploy behavior:

- Deploy job (self-hosted `zivo` runner): `scripts/deploy-vps-local-build.sh`
  builds and pushes `zivo-migrate`, `zivo-auth`, `zivo-storage`, `zivo-practice`,
  `zivo-content`, `zivo-study`, `zivo-library`, `zivo-admin`, `zivo-worker`,
  and `zivo-web`, tagged `Zivo_0.1.<run>`.
- Alembic Jobs run per service image before rolling HTTP deployments.
- Product schema uses `ghcr.io/<owner>/zivo-migrate`. Workers use
  `ghcr.io/<owner>/zivo-worker`. There is no `zivo-api` Deployment.
- After rollout, `scripts/promote-prod-image-tags.sh` commits prod tags to
  `main`.

## Prod VPS / Kubernetes monitoring

Prod is a single VPS running K3s. SSH alias:

```bash
export KUBECONFIG=~/.kube/zivo-ha.conf
```

Then set the kubeconfig for kubectl:

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
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
```

Inspect the migration job that runs before rollout:

```bash
KUBECONFIG=~/.kube/zivo-ha.conf kubectl -n zivo logs job/alembic-migrate
```

For each rollout, verify:

- Deployments are available.
- The `alembic-migrate` Job completed successfully.
- Old zivo-api pods are gone (that release is uninstalled).
- New auth/storage/practice/content/study/library/admin, web, worker-eta-cpu, worker-eta-io pods are running.
- Running pod images use the new `Zivo_0.1.<run>` tag.
- No relevant pods are in `CrashLoopBackOff`, `ImagePullBackOff`, `ErrImagePull`,
  `Pending`, or repeatedly restarting.

Useful waits, still read-only:

```bash
KUBECONFIG=~/.kube/zivo-ha.conf kubectl -n zivo rollout status deployment/zivo-auth --timeout=10m
KUBECONFIG=~/.kube/zivo-ha.conf kubectl -n zivo rollout status deployment/worker-eta-cpu --timeout=10m
KUBECONFIG=~/.kube/zivo-ha.conf kubectl -n zivo rollout status deployment/worker-eta-io --timeout=10m
```

If a deployment name differs from the Helm release name, inspect current
deployments with `kubectl -n zivo get deploy` and use the actual deployment
name. Do not restart, delete, patch, or sync anything without a new
state-changing approval.

## Stop conditions

Stop and report before continuing if:

- release notes cannot be produced
- the deploy workflow fails
- the `alembic-migrate` Job fails
- the automated promotion PR fails or does not merge
- any required pod stays unhealthy
- running pod images do not match the new image tag
- SSH to the prod VPS fails

## Final response

Include:

- deploy workflow run URL and result
- deployed image tag (`Zivo_0.1.<run>`)
- pod replacement / rollout result
- migration Job result
- release notes report path
- anything the user must manually send or verify

Do not claim deployment success unless the workflow succeeded, the migration
Job completed, and pod replacement was verified.
