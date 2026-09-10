# cicd pipeline

Single pipeline for zivo.fyi: **unit tests → build + push images to GHCR → production approval gate → helm tag update (ArgoCD rollout) → GitHub release**.

All jobs run strictly sequentially on the self-hosted `zivo` runner:

```
unit-tests (backend, auth, storage, practice, content, study, library, admin)
   → build (resolve tag + build/push zivo-python-deps)
      → images (10 service images)
         → promote [main only, production gate]
              → rewrite tags in infra/k8s/environments/prod/*-values.yaml
              → commit + push "chore: promote <tag>" (ArgoCD syncs)
              → create GitHub release v<tag>
```

**Tags:** semver on `main` (patch auto-bumped from the latest `v*` git tag, or the manual `version` input); `sha-<short sha>` on every other branch. Helm tags and GitHub releases happen **only for `main`**; branch builds only test and push `sha-` tagged images.

**Triggers:** push to any branch (except docs/infra/markdown-only changes), pull requests to `main` (tests only), and manual dispatch.

---

## Steps before running the pipeline (one-time setup)

### 1. Create a GHCR personal access token

The pipeline never uses the workflow's ephemeral `GITHUB_TOKEN` for pushes; it logs in with environment secrets.

1. GitHub → your avatar → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)**.
2. **Generate new token (classic)** with scopes:
   - `write:packages` (push images)
   - `read:packages` (pull images)
   - `repo` (only needed if your repository/packages are private)
3. Copy the token; you will not see it again.

### 2. Create the `ghcr` environment (image push credentials)

1. Repository → **Settings** → **Environments** → **New environment** → name it exactly `ghcr`.
2. Add **no protection rules** (no required reviewers; builds must never wait for approval).
3. **Add environment secret** twice:
   - `GHCR_USERNAME` → your GitHub username.
   - `GHCR_TOKEN` → the PAT from step 1.

The `build` and `images` jobs declare `environment: ghcr`, which is what gives them these secrets.

### 3. Create the `production` environment (the manual gate)

1. Repository → **Settings** → **Environments** → **New environment** → name it exactly `production`.
2. Under **Deployment protection rules**, enable **Required reviewers** and add the people who may approve a release.
3. Save.

This reviewers list **is** the manual gate: on `main`, the `promote` job pauses in *Waiting* until someone approves. No reviewers configured means no gate.

### 4. Prepare the self-hosted runner (worker node4)

The `gh_runner` Ansible role (`infra/ansible/roles/gh_runner/`) installs Docker + buildx and registers the runner. Two tools are not covered by Ansible and must be present once:

```bash
# yq (mikefarah v4 syntax, used by the promote step to edit helm values)
sudo curl -fsSL -o /usr/local/bin/yq \
  https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64
sudo chmod +x /usr/local/bin/yq

# gh CLI (used by the release step; no login needed, the workflow passes GH_TOKEN)
(type -p gh >/dev/null || (sudo curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg) && \
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null && sudo apt update && sudo apt install -y gh)
```

Verify: `docker buildx version`, `yq --version`, `gh --version`.

### 5. Confirm ArgoCD is watching the repo (bootstrap already done)

The `argocd` Ansible role installs ArgoCD once and seeds the Applications from `infra/argocd/`. After that, ArgoCD auto-syncs every release from `main`. Nothing to do per-deploy; verify with `kubectl -n argocd get applications` (kubeconfig `~/.kube/zivo-ha.conf`) if unsure.

---

## Running the pipeline

| Action | Result |
|--------|--------|
| Push to any branch | Tests + `sha-` tagged images to GHCR |
| Open/update a PR to `main` | Unit tests only |
| Push to `main` | Full release flow (gate included) |
| Manual dispatch | Same as a push to the chosen branch |

Manual dispatch: **Actions → cicd → Run workflow**, pick the branch, optionally set `version` (e.g. `1.0.0` or `2.0.0` to force a major bump). CLI equivalent:

```bash
gh workflow run cicd --ref main              # auto patch-bump
gh workflow run cicd --ref main -f version=1.2.4
```

When the `promote` job shows **Waiting**, a required reviewer approves it from the run page (or `gh run view <id>` / the Actions UI). Approval triggers: helm values update → push → ArgoCD rollout → GitHub release.

**First run on `main`:** with no `v*` tags in the repo the semver starts at `0.0.1`. Dispatch with `-f version=1.0.0` if you want a different starting point.

---

## Before you dispatch: pre-flight checklist

1. `./scripts/ship-gates.sh` passes locally (CI only runs unit tests; lint and the frontend build are your responsibility). See [agents/ship-gates.md](../../agents/ship-gates.md).
2. If the release changes schema, run migrations manually before approving the gate: `scripts/run-k8s-schema-migrate.sh` from a workstation with `KUBECONFIG=~/.kube/zivo-ha.conf`. The pipeline does not run Alembic.
3. Enough disk on the runner box for a full image build wave (`docker builder prune -af` if low; a daily cleanup timer from `gh_runner_cleanup` handles this normally).

## Where things land

| Artifact | Location |
|----------|----------|
| Images | `ghcr.io/<owner>/zivo-{python-deps,migrate,auth,storage,practice,content,study,library,admin,worker,web}:<tag>` |
| Helm tag update | `infra/k8s/environments/prod/*-values.yaml` (bot commit `chore: promote <tag> [skip ci]`) |
| Rollout | ArgoCD sync of the 13 Applications in namespace `zivo` |
| Release | GitHub → Releases → `v<tag>` with auto-generated notes |
