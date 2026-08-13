#!/usr/bin/env bash
# Build images on the CI runner (the small Judge0 box), push them to GHCR, then roll out to the prod
# K3s over the network (KUBECONFIG points at the prod API; k3s pulls the public images from GHCR).
# Requires a prior `docker login ghcr.io` for the push. Set PREBUILT=1 to skip build+push and just
# roll out tags already in GHCR. Run by the Deploy Zivo workflow; also runnable by hand.
set -euo pipefail

# The web Dockerfile uses `RUN --mount=type=cache`, which needs BuildKit; the box's docker
# defaults to the legacy builder, so force BuildKit on.
export DOCKER_BUILDKIT=1

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

# The self-hosted runner (github-runner) can't read root's k3s.yaml (0600 root:root), so helm/kubectl
# fail with "Kubernetes cluster unreachable ... permission denied". Stage a runner-readable copy using
# the runner's scoped passwordless sudo (limited to `k3s` — not cp/install). Regenerated each deploy,
# so k3s restarts (which reset k3s.yaml to 0600) never break us.
if [[ ! -r "$KUBECONFIG" ]]; then
  RUNNER_KUBECONFIG="${HOME}/.kube/config"
  mkdir -p "$(dirname "$RUNNER_KUBECONFIG")"
  sudo -n k3s kubectl config view --raw > "$RUNNER_KUBECONFIG"
  chmod 600 "$RUNNER_KUBECONFIG"
  export KUBECONFIG="$RUNNER_KUBECONFIG"
fi
TAG="${1:-Zivo_0.1.$(date +%Y%m%d%H%M)}"
REPO="${REPO:-/opt/zivo}"
OWNER="${OWNER:-benitojd}"
API_IMAGE="ghcr.io/${OWNER}/zivo-api:${TAG}"
AUTH_IMAGE="ghcr.io/${OWNER}/zivo-auth:${TAG}"
STORAGE_IMAGE="ghcr.io/${OWNER}/zivo-storage:${TAG}"
PRACTICE_IMAGE="ghcr.io/${OWNER}/zivo-practice:${TAG}"
CONTENT_IMAGE="ghcr.io/${OWNER}/zivo-content:${TAG}"
STUDY_IMAGE="ghcr.io/${OWNER}/zivo-study:${TAG}"
WEB_IMAGE="ghcr.io/${OWNER}/zivo-web:${TAG}"
NS=zivo
ROLLBACK_RELEASES=()

rollback() {
  local code=$?
  if [[ $code -ne 0 && ${#ROLLBACK_RELEASES[@]} -gt 0 ]]; then
    echo "Deploy failed (exit $code) — rolling back Helm releases..."
    for entry in "${ROLLBACK_RELEASES[@]}"; do
      release="${entry%%:*}"
      rev="${entry##*:}"
      echo "helm rollback $release $rev"
      helm rollback "$release" "$rev" -n "$NS" || true
    done
  fi
  exit "$code"
}
trap rollback ERR

helm_record() {
  local release=$1
  local rev
  rev=$(helm history "$release" -n "$NS" --max 1 -o json 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['revision'])" 2>/dev/null || echo "0")
  if [[ "$rev" == "0" ]]; then
    return 0
  fi
  ROLLBACK_RELEASES+=("${release}:${rev}")
}

rollout_wait() {
  local kind=$1 name=$2 timeout=${3:-10m}
  kubectl -n "$NS" rollout status "$kind/$name" --timeout="$timeout"
}

wait_job() {
  local name=$1
  local timeout=${2:-15m}
  echo "Waiting for job/${name} to complete"
  if ! kubectl -n "$NS" wait --for=condition=complete "job/${name}" --timeout="${timeout}"; then
    echo "Job ${name} did not complete" >&2
    kubectl -n "$NS" logs "job/${name}" --tail=200 || true
    kubectl -n "$NS" describe "job/${name}" || true
    exit 1
  fi
}

ensure_ghcr_pull_secret() {
  PULL_SECRET_SET=()
  if [[ -z "${GITHUB_TOKEN:-}" ]]; then
    # Self-hosted jobs often omit GITHUB_TOKEN from the script env; docker login
    # already stored the Actions token in ~/.docker/config.json.
    GITHUB_TOKEN="$(python3 - <<'PY'
import json, base64, pathlib, sys
path = pathlib.Path.home() / ".docker" / "config.json"
try:
    cfg = json.loads(path.read_text())
except Exception:
    sys.exit(0)
auth = (cfg.get("auths") or {}).get("ghcr.io", {}).get("auth")
if not auth:
    sys.exit(0)
print(base64.b64decode(auth).decode().split(":", 1)[-1], end="")
PY
)"
  fi
  if [[ -z "${GITHUB_TOKEN:-}" ]]; then
    echo "GITHUB_TOKEN is required: k3s anonymous GHCR pull returns 401 for new tags" >&2
    exit 1
  fi
  kubectl -n "$NS" create secret docker-registry ghcr-pull \
    --docker-server=ghcr.io \
    --docker-username="${GITHUB_ACTOR:-${OWNER}}" \
    --docker-password="${GITHUB_TOKEN}" \
    --dry-run=client -o yaml | kubectl apply -f -
  echo "Applied ghcr-pull secret for k3s image pulls"
  PULL_SECRET_SET=(--set "imagePullSecrets[0].name=ghcr-pull")
}

# --- Preflight: disk-space gates ---------------------------------------------
# 2026-08-09 outage: the prod node hit kubelet disk-pressure MID-ROLLOUT (orphaned
# Docker stores had filled the disk); pods were evicted, postgres included, and the
# pgbouncer helm --wait timed out. Fail fast here instead — a deploy must never be
# the thing that tips the node over the eviction threshold.
MIN_RUNNER_FREE_GB="${MIN_RUNNER_FREE_GB:-8}"   # docker build scratch on this box
MIN_NODE_FREE_GB="${MIN_NODE_FREE_GB:-20}"      # image pull + rollout headroom on prod

runner_free_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
if (( runner_free_gb < MIN_RUNNER_FREE_GB )); then
  echo "PREFLIGHT FAIL: runner has ${runner_free_gb}G free (< ${MIN_RUNNER_FREE_GB}G)." \
       "Run 'docker builder prune -af' / drop old image tags on this box and retry." >&2
  exit 1
fi

NODE="$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')"
if kubectl get node "$NODE" \
     -o jsonpath='{range .status.conditions[?(@.type=="DiskPressure")]}{.status}{end}' \
     | grep -q True; then
  echo "PREFLIGHT FAIL: node ${NODE} is under DiskPressure — free disk on the VPS before deploying." >&2
  exit 1
fi
node_free_bytes=$(kubectl get --raw "/api/v1/nodes/${NODE}/proxy/stats/summary" 2>/dev/null \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["node"]["fs"]["availableBytes"])' \
  2>/dev/null || echo "")
if [[ -n "$node_free_bytes" ]]; then
  node_free_gb=$(( node_free_bytes / 1024 / 1024 / 1024 ))
  if (( node_free_gb < MIN_NODE_FREE_GB )); then
    echo "PREFLIGHT FAIL: prod node ${NODE} has ${node_free_gb}G free (< ${MIN_NODE_FREE_GB}G)." \
         "Free disk on the VPS (check /var/lib/containerd + /var/lib/docker for orphaned" \
         "Docker stores — see 2026-08-09 outage) before deploying." >&2
    exit 1
  fi
  echo "Preflight OK: runner ${runner_free_gb}G free, node ${NODE} ${node_free_gb}G free"
else
  # ponytail: stats API unreadable -> rely on the DiskPressure condition alone.
  echo "Preflight: node fs stats unavailable; DiskPressure=False, continuing" >&2
fi

cd "$REPO"
git fetch origin main
git reset --hard origin/main
echo "Deploying $(git rev-parse --short HEAD) as ${TAG}"

if [[ "${PREBUILT:-0}" != "1" ]]; then
  docker build --pull -t "${API_IMAGE}" -f backend/Dockerfile --target runtime backend/
  docker build --pull -t "${AUTH_IMAGE}" -f auth/Dockerfile --target runtime auth/
  docker build --pull -t "${STORAGE_IMAGE}" -f storage/Dockerfile --target runtime storage/
  docker build --pull -t "${PRACTICE_IMAGE}" -f practice/Dockerfile --target runtime .
  docker build --pull -t "${CONTENT_IMAGE}" -f content/Dockerfile --target runtime .
  docker build --pull -t "${STUDY_IMAGE}" -f study/Dockerfile --target runtime .
  docker build --pull -t "${WEB_IMAGE}" -f frontend/Dockerfile --target runtime \
    --build-arg NEXT_PUBLIC_API_URL= \
    --build-arg NEXT_PUBLIC_AUTH_URL= \
    --build-arg NEXT_PUBLIC_STORAGE_URL= \
    --build-arg API_PROXY_URL=http://zivo-api:8000 \
    --build-arg AUTH_PROXY_URL=http://zivo-auth:8000 \
    --build-arg STORAGE_PROXY_URL=http://zivo-storage:8000 \
    --build-arg PRACTICE_PROXY_URL=http://zivo-practice:8000 \
    --build-arg CONTENT_PROXY_URL=http://zivo-content:8000 \
    --build-arg STUDY_PROXY_URL=http://zivo-study:8000 \
    frontend/
  # Push to GHCR; prod k3s pulls these (images are public, unique tag per deploy).
  docker push "${API_IMAGE}"
  docker push "${AUTH_IMAGE}"
  docker push "${STORAGE_IMAGE}"
  docker push "${PRACTICE_IMAGE}"
  docker push "${CONTENT_IMAGE}"
  docker push "${STUDY_IMAGE}"
  docker push "${WEB_IMAGE}"
else
  echo "PREBUILT=1 — ${API_IMAGE}, ${AUTH_IMAGE}, ${STORAGE_IMAGE}, ${PRACTICE_IMAGE}, ${CONTENT_IMAGE}, ${STUDY_IMAGE}, and ${WEB_IMAGE} assumed already in GHCR"
fi

ensure_ghcr_pull_secret

helm_record pgbouncer
if [[ -d ./infra/k8s/charts/pgbouncer ]]; then
  helm upgrade --install pgbouncer ./infra/k8s/charts/pgbouncer -n "$NS" \
    -f infra/k8s/environments/prod/pgbouncer-values.yaml \
    --wait --timeout 5m
  rollout_wait deployment pgbouncer 5m
  if [[ -x ./scripts/wire-pgbouncer-database-url.sh ]]; then
    ./scripts/wire-pgbouncer-database-url.sh "$NS" || true
  fi
fi

# Run additive schema migrations BEFORE rolling the app, so new pods boot against a
# schema that already has the columns/tables their code reads (e.g. a new ORM column
# is SELECTed on boot). Migrations MUST stay additive + backward-compatible: the still
# -running old pods keep working against the new schema during the rolling update.
# (Image was built + pushed above, so the alembic job can pull this TAG.)
# Auth schema first (auth.account), then product schema (qb.account stub + copy).
kubectl -n "$NS" delete job auth-alembic-migrate --ignore-not-found=true
helm upgrade --install auth-schema ./infra/k8s/charts/db-schema -n "$NS" \
  -f infra/k8s/environments/prod/backend-release-values.yaml \
  --set image.tag="${TAG}" \
  --set image.repository="ghcr.io/${OWNER}/zivo-auth" \
  --set namespace="$NS" \
  --set jobName=auth-alembic-migrate \
  "${PULL_SECRET_SET[@]}" \
  --wait --wait-for-jobs --timeout 15m
wait_job auth-alembic-migrate 15m

kubectl -n "$NS" delete job storage-alembic-migrate --ignore-not-found=true
helm upgrade --install storage-schema ./infra/k8s/charts/db-schema -n "$NS" \
  -f infra/k8s/environments/prod/backend-release-values.yaml \
  --set image.tag="${TAG}" \
  --set image.repository="ghcr.io/${OWNER}/zivo-storage" \
  --set namespace="$NS" \
  --set jobName=storage-alembic-migrate \
  "${PULL_SECRET_SET[@]}" \
  --wait --wait-for-jobs --timeout 15m
wait_job storage-alembic-migrate 15m

kubectl -n "$NS" delete job practice-alembic-migrate --ignore-not-found=true
helm upgrade --install practice-schema ./infra/k8s/charts/db-schema -n "$NS" \
  -f infra/k8s/environments/prod/backend-release-values.yaml \
  --set image.tag="${TAG}" \
  --set image.repository="ghcr.io/${OWNER}/zivo-practice" \
  --set namespace="$NS" \
  --set jobName=practice-alembic-migrate \
  "${PULL_SECRET_SET[@]}" \
  --wait --wait-for-jobs --timeout 15m
wait_job practice-alembic-migrate 15m

kubectl -n "$NS" delete job content-alembic-migrate --ignore-not-found=true
helm upgrade --install content-schema ./infra/k8s/charts/db-schema -n "$NS" \
  -f infra/k8s/environments/prod/backend-release-values.yaml \
  --set image.tag="${TAG}" \
  --set image.repository="ghcr.io/${OWNER}/zivo-content" \
  --set namespace="$NS" \
  --set jobName=content-alembic-migrate \
  "${PULL_SECRET_SET[@]}" \
  --wait --wait-for-jobs --timeout 15m
wait_job content-alembic-migrate 15m

kubectl -n "$NS" delete job study-alembic-migrate --ignore-not-found=true
helm upgrade --install study-schema ./infra/k8s/charts/db-schema -n "$NS" \
  -f infra/k8s/environments/prod/backend-release-values.yaml \
  --set image.tag="${TAG}" \
  --set image.repository="ghcr.io/${OWNER}/zivo-study" \
  --set namespace="$NS" \
  --set jobName=study-alembic-migrate \
  "${PULL_SECRET_SET[@]}" \
  --wait --wait-for-jobs --timeout 15m
wait_job study-alembic-migrate 15m

kubectl -n "$NS" delete job alembic-migrate --ignore-not-found=true
helm upgrade --install db-schema ./infra/k8s/charts/db-schema -n "$NS" \
  -f infra/k8s/environments/prod/backend-release-values.yaml \
  --set image.tag="${TAG}" \
  --set image.repository="ghcr.io/${OWNER}/zivo-api" \
  --set namespace="$NS" \
  --set jobName=alembic-migrate \
  "${PULL_SECRET_SET[@]}" \
  --wait --wait-for-jobs --timeout 15m
wait_job alembic-migrate 15m

# Note: code execution (Judge0) runs on a dedicated box, not in this cluster — see
# infra/judge0/. The API reaches it via JUDGE0_URL (set in the api chart values).

for release in zivo-worker-io zivo-worker-cpu; do
  helm_record "$release"
  # Each release owns one primary workload. Newspaper (Telethon) rides with CPU
  # only — shared values would otherwise create the same Deployment/PDB twice.
  io=true cpu=false newspaper=false
  if [[ "$release" == *cpu* ]]; then
    io=false
    cpu=true
    newspaper=true
  fi
  helm upgrade --install "$release" ./infra/k8s/charts/worker -n "$NS" \
    -f infra/k8s/environments/prod/worker-values.yaml \
    --set image.tag="${TAG}" \
    --set "workloads.io.enabled=${io}" \
    --set "workloads.cpu.enabled=${cpu}" \
    --set "workloads.newspaper.enabled=${newspaper}" \
    "${PULL_SECRET_SET[@]}" \
    --wait --timeout 15m
  rollout_wait deployment "${release#zivo-}" 15m
done

helm_record zivo-auth
helm upgrade --install zivo-auth ./infra/k8s/charts/auth -n "$NS" \
  -f infra/k8s/environments/prod/auth-values.yaml \
  --set image.tag="${TAG}" \
  "${PULL_SECRET_SET[@]}" \
  --wait --timeout 20m
rollout_wait deployment zivo-auth 20m

helm_record zivo-storage
helm upgrade --install zivo-storage ./infra/k8s/charts/storage -n "$NS" \
  -f infra/k8s/environments/prod/storage-values.yaml \
  --set image.tag="${TAG}" \
  "${PULL_SECRET_SET[@]}" \
  --wait --timeout 20m
rollout_wait deployment zivo-storage 20m

helm_record zivo-practice
helm upgrade --install zivo-practice ./infra/k8s/charts/practice -n "$NS" \
  -f infra/k8s/environments/prod/practice-values.yaml \
  --set image.tag="${TAG}" \
  "${PULL_SECRET_SET[@]}" \
  --wait --timeout 20m
rollout_wait deployment zivo-practice 20m

helm_record zivo-content
helm upgrade --install zivo-content ./infra/k8s/charts/content -n "$NS" \
  -f infra/k8s/environments/prod/content-values.yaml \
  --set image.tag="${TAG}" \
  "${PULL_SECRET_SET[@]}" \
  --wait --timeout 20m
rollout_wait deployment zivo-content 20m

helm_record zivo-study
helm upgrade --install zivo-study ./infra/k8s/charts/study -n "$NS" \
  -f infra/k8s/environments/prod/study-values.yaml \
  --set image.tag="${TAG}" \
  "${PULL_SECRET_SET[@]}" \
  --wait --timeout 20m
rollout_wait deployment zivo-study 20m

helm_record zivo-api
helm upgrade --install zivo-api ./infra/k8s/charts/api -n "$NS" \
  -f infra/k8s/environments/prod/backend-release-values.yaml \
  -f infra/k8s/environments/prod/api-values.yaml \
  --set image.tag="${TAG}" \
  "${PULL_SECRET_SET[@]}" \
  --wait --timeout 20m
rollout_wait deployment zivo-api 20m

helm_record zivo-web
helm upgrade --install zivo-web ./infra/k8s/charts/web -n "$NS" \
  -f infra/k8s/environments/prod/web-values.yaml \
  --set image.tag="${TAG}" \
  "${PULL_SECRET_SET[@]}" \
  --wait --timeout 20m
rollout_wait deployment zivo-web 20m

kubectl -n "$NS" get pods -o wide
echo "Done: ${TAG}"
