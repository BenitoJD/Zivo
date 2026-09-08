#!/usr/bin/env bash
# Build images on the CI runner (node4), push them to GHCR, then Argo CD / helm roll out to the
# HA cluster (KUBECONFIG points at the API LB via the runner's kubeconfig).
# Requires a prior `docker login ghcr.io` for the push. Set PREBUILT=1 to skip build+push and just
# roll out tags already in GHCR. Run by the Deploy Zivo workflow; also runnable by hand.
set -euo pipefail

# The web Dockerfile uses `RUN --mount=type=cache`, which needs BuildKit; the box's docker
# defaults to the legacy builder, so force BuildKit on.
export DOCKER_BUILDKIT=1

# The cluster kubeconfig is staged by the workflow env (KUBECONFIG=/home/github-runner/.kube/config).
# On the new HA cluster, this file was installed by the gh_runner ansible role and points at
# the API load balancer (zivo-lb2:8443). No k3s fallback needed.
if [[ ! -s "$KUBECONFIG" ]]; then
  echo "ERROR: KUBECONFIG ($KUBECONFIG) is empty or missing — cannot deploy" >&2
  exit 1
fi
TAG="${1:-Zivo_0.1.$(date +%Y%m%d%H%M)}"
REPO="${REPO:-/opt/zivo}"
OWNER="${OWNER:-benitojd}"
DEPS_IMAGE="ghcr.io/${OWNER}/zivo-python-deps:${TAG}"
MIGRATE_IMAGE="ghcr.io/${OWNER}/zivo-migrate:${TAG}"
AUTH_IMAGE="ghcr.io/${OWNER}/zivo-auth:${TAG}"
STORAGE_IMAGE="ghcr.io/${OWNER}/zivo-storage:${TAG}"
PRACTICE_IMAGE="ghcr.io/${OWNER}/zivo-practice:${TAG}"
CONTENT_IMAGE="ghcr.io/${OWNER}/zivo-content:${TAG}"
STUDY_IMAGE="ghcr.io/${OWNER}/zivo-study:${TAG}"
LIBRARY_IMAGE="ghcr.io/${OWNER}/zivo-library:${TAG}"
ADMIN_IMAGE="ghcr.io/${OWNER}/zivo-admin:${TAG}"
WORKER_IMAGE="ghcr.io/${OWNER}/zivo-worker:${TAG}"
WEB_IMAGE="ghcr.io/${OWNER}/zivo-web:${TAG}"
NS=zivo
ROLLBACK_RELEASES=()

rollback() {
  local code=$?
  if [[ $code -ne 0 && ${#ROLLBACK_RELEASES[@]} -gt 0 ]]; then
    echo "Deploy failed (exit $code) — rolling back only releases left in a failed state..."
    for entry in "${ROLLBACK_RELEASES[@]}"; do
      release="${entry%%:*}"
      rev="${entry##*:}"
      # A release that actually rolled out stays; reverting it would un-ship
      # working services and re-trigger rollout churn on a busy cluster.
      status=$(helm status "$release" -n "$NS" -o json 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('info', {}).get('status', ''))" 2>/dev/null || echo "")
      case "$status" in
        failed|pending-upgrade|pending-install|pending-rollback)
          echo "helm rollback $release $rev (status: $status)"
          helm rollback "$release" "$rev" -n "$NS" || true
          ;;
        *)
          echo "keep $release (status: ${status:-unknown})"
          ;;
      esac
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
  # The operator manages ghcr-pull via the platform_data Ansible role from the
  # vaulted ghcr_pull_token. The workflow's server-to-server token works for
  # docker login HERE but GitHub 403s it when kubelet exchanges it from the
  # cluster nodes — overwriting the secret with it broke every authenticated
  # pull fleet-wide (2026-09-07). Keep the existing secret; create only if absent.
  if kubectl -n "$NS" get secret ghcr-pull >/dev/null 2>&1; then
    echo "ghcr-pull secret exists (operator-managed) — keeping it"
    PULL_SECRET_SET=(--set "imagePullSecrets[0].name=ghcr-pull")
    return 0
  fi
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

DEPS_BUILD_ARGS=("--build-arg" "PYTHON_DEPS_IMAGE=${DEPS_IMAGE}")
BUILD_LOG_DIR="$(mktemp -d /tmp/zivo-build-logs.XXXXXX)"
BUILD_FAILURES=()

# Run builds/pushes concurrently; each job logs to its own file so a failure
# is printable without interleaving. The runner box has ~4 spare cores, so
# waves of 4 saturate it without thrashing BuildKit.
run_jobs() {
  local pids=() names=() i failed
  for i in "${!JOB_CMDS[@]}"; do
    names+=("${JOB_NAMES[$i]}")
    ( eval "${JOB_CMDS[$i]}" ) > "${BUILD_LOG_DIR}/${JOB_NAMES[$i]}.log" 2>&1 &
    pids+=($!)
  done
  failed=0
  for i in "${!pids[@]}"; do
    if wait "${pids[$i]}"; then
      echo "  ok   ${names[$i]}"
    else
      echo "  FAIL ${names[$i]} (log: ${BUILD_LOG_DIR}/${names[$i]}.log)"
      BUILD_FAILURES+=("${names[$i]}")
      failed=1
    fi
  done
  if [[ $failed -ne 0 ]]; then
    for name in "${BUILD_FAILURES[@]}"; do
      echo "----- ${name} log tail -----"
      tail -30 "${BUILD_LOG_DIR}/${name}.log" || true
    done
    exit 1
  fi
}

if [[ "${PREBUILT:-0}" != "1" ]]; then
  # The shared venv builds ONCE per deploy; every backend service image
  # inherits it, so the heavy layers are downloaded, built, pushed, and
  # pulled by each node exactly once (see docker/python-deps.Dockerfile).
  #
  # Builds run in parallel waves: wave 1 is everything that does not depend
  # on the deps image (web/auth/storage plus deps itself); wave 2 is the
  # seven images that inherit it. No --pull on the inheriting builds:
  # BuildKit would resolve the freshly built deps image from the registry;
  # the local tag is the freshest copy.

  echo "== build wave 1: deps + web + auth + storage (parallel) =="
  JOB_NAMES=(); JOB_CMDS=()
  JOB_NAMES+=("deps");    JOB_CMDS+=("docker build --pull -t '${DEPS_IMAGE}' -f docker/python-deps.Dockerfile .")
  JOB_NAMES+=("web");     JOB_CMDS+=("docker build --pull -t '${WEB_IMAGE}' -f frontend/Dockerfile --target runtime --build-arg NEXT_PUBLIC_API_URL= --build-arg NEXT_PUBLIC_AUTH_URL= --build-arg NEXT_PUBLIC_STORAGE_URL= --build-arg AUTH_PROXY_URL=http://zivo-auth:8000 --build-arg STORAGE_PROXY_URL=http://zivo-storage:8000 --build-arg PRACTICE_PROXY_URL=http://zivo-practice:8000 --build-arg CONTENT_PROXY_URL=http://zivo-content:8000 --build-arg STUDY_PROXY_URL=http://zivo-study:8000 --build-arg LIBRARY_PROXY_URL=http://zivo-library:8000 --build-arg ADMIN_PROXY_URL=http://zivo-admin:8000 frontend/")
  JOB_NAMES+=("auth");    JOB_CMDS+=("docker build --pull -t '${AUTH_IMAGE}' -f auth/Dockerfile --target runtime auth/")
  JOB_NAMES+=("storage"); JOB_CMDS+=("docker build --pull -t '${STORAGE_IMAGE}' -f storage/Dockerfile --target runtime storage/")
  run_jobs

  echo "== push wave 1 (parallel) =="
  JOB_NAMES=(); JOB_CMDS=()
  for name in deps web auth storage; do
    JOB_NAMES+=("push-$name")
    case "$name" in
      deps)    JOB_CMDS+=("docker push '${DEPS_IMAGE}'") ;;
      web)     JOB_CMDS+=("docker push '${WEB_IMAGE}'") ;;
      auth)    JOB_CMDS+=("docker push '${AUTH_IMAGE}'") ;;
      storage) JOB_CMDS+=("docker push '${STORAGE_IMAGE}'") ;;
    esac
  done
  run_jobs

  echo "== build wave 2: deps-inheriting images (parallel) =="
  JOB_NAMES=(); JOB_CMDS=()
  JOB_NAMES+=("migrate");  JOB_CMDS+=("docker build -t '${MIGRATE_IMAGE}' -f backend/Dockerfile --target runtime ${DEPS_BUILD_ARGS[*]} backend/")
  JOB_NAMES+=("practice"); JOB_CMDS+=("docker build -t '${PRACTICE_IMAGE}' -f practice/Dockerfile --target runtime ${DEPS_BUILD_ARGS[*]} .")
  JOB_NAMES+=("content");  JOB_CMDS+=("docker build -t '${CONTENT_IMAGE}' -f content/Dockerfile --target runtime ${DEPS_BUILD_ARGS[*]} .")
  JOB_NAMES+=("study");    JOB_CMDS+=("docker build -t '${STUDY_IMAGE}' -f study/Dockerfile --target runtime ${DEPS_BUILD_ARGS[*]} .")
  JOB_NAMES+=("library");  JOB_CMDS+=("docker build -t '${LIBRARY_IMAGE}' -f library/Dockerfile --target runtime ${DEPS_BUILD_ARGS[*]} .")
  JOB_NAMES+=("admin");    JOB_CMDS+=("docker build -t '${ADMIN_IMAGE}' -f admin/Dockerfile --target runtime ${DEPS_BUILD_ARGS[*]} .")
  JOB_NAMES+=("worker");   JOB_CMDS+=("docker build -t '${WORKER_IMAGE}' -f workers/Dockerfile --target runtime ${DEPS_BUILD_ARGS[*]} .")
  run_jobs

  echo "== push wave 2 (parallel) =="
  JOB_NAMES=(); JOB_CMDS=()
  JOB_NAMES+=("push-migrate");  JOB_CMDS+=("docker push '${MIGRATE_IMAGE}'")
  JOB_NAMES+=("push-practice"); JOB_CMDS+=("docker push '${PRACTICE_IMAGE}'")
  JOB_NAMES+=("push-content");  JOB_CMDS+=("docker push '${CONTENT_IMAGE}'")
  JOB_NAMES+=("push-study");    JOB_CMDS+=("docker push '${STUDY_IMAGE}'")
  JOB_NAMES+=("push-library");  JOB_CMDS+=("docker push '${LIBRARY_IMAGE}'")
  JOB_NAMES+=("push-admin");    JOB_CMDS+=("docker push '${ADMIN_IMAGE}'")
  JOB_NAMES+=("push-worker");   JOB_CMDS+=("docker push '${WORKER_IMAGE}'")
  run_jobs
else
  echo "PREBUILT=1 - ${MIGRATE_IMAGE}, ${AUTH_IMAGE}, ${STORAGE_IMAGE}, ${PRACTICE_IMAGE}, ${CONTENT_IMAGE}, ${STUDY_IMAGE}, ${LIBRARY_IMAGE}, ${ADMIN_IMAGE}, ${WORKER_IMAGE}, and ${WEB_IMAGE} assumed already in GHCR"
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

kubectl -n "$NS" delete job library-alembic-migrate --ignore-not-found=true
helm upgrade --install library-schema ./infra/k8s/charts/db-schema -n "$NS" \
  -f infra/k8s/environments/prod/backend-release-values.yaml \
  --set image.tag="${TAG}" \
  --set image.repository="ghcr.io/${OWNER}/zivo-library" \
  --set namespace="$NS" \
  --set jobName=library-alembic-migrate \
  "${PULL_SECRET_SET[@]}" \
  --wait --wait-for-jobs --timeout 15m
wait_job library-alembic-migrate 15m

kubectl -n "$NS" delete job admin-alembic-migrate --ignore-not-found=true
helm upgrade --install admin-schema ./infra/k8s/charts/db-schema -n "$NS" \
  -f infra/k8s/environments/prod/backend-release-values.yaml \
  --set image.tag="${TAG}" \
  --set image.repository="ghcr.io/${OWNER}/zivo-admin" \
  --set namespace="$NS" \
  --set jobName=admin-alembic-migrate \
  "${PULL_SECRET_SET[@]}" \
  --wait --wait-for-jobs --timeout 15m
wait_job admin-alembic-migrate 15m

kubectl -n "$NS" delete job alembic-migrate --ignore-not-found=true
helm upgrade --install db-schema ./infra/k8s/charts/db-schema -n "$NS" \
  -f infra/k8s/environments/prod/backend-release-values.yaml \
  --set image.tag="${TAG}" \
  --set image.repository="ghcr.io/${OWNER}/zivo-migrate" \
  --set namespace="$NS" \
  --set jobName=alembic-migrate \
  "${PULL_SECRET_SET[@]}" \
  --wait --wait-for-jobs --timeout 15m
wait_job alembic-migrate 15m

# Note: code execution (Judge0) runs on a dedicated box, not in this cluster (see
# infra/judge0/). Practice reaches it via JUDGE0_URL (set in the practice chart).

# ROLLOUT_MODE=argocd (the default): this script builds + pushes images and runs
# schema migrations, then the workflow promotes the image tags in git and ArgoCD
# rolls every service/worker release from the chart values. Helm must not touch
# ArgoCD-managed releases — two controllers fighting over one cluster caused the
# 2026-09-06 rollout storm (helm upgrades reverted by auto-sync mid-rollout).
# ROLLOUT_MODE=helm keeps the legacy direct helm rollout for break-glass use.
ROLLOUT_MODE="${ROLLOUT_MODE:-argocd}"
if [[ "$ROLLOUT_MODE" == "argocd" ]]; then
  echo "ROLLOUT_MODE=argocd — migrations done; ArgoCD will roll releases from the promoted tags."
  exit 0
fi

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
    --set image.repository="ghcr.io/${OWNER}/zivo-worker" \
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

helm_record zivo-library
helm upgrade --install zivo-library ./infra/k8s/charts/library -n "$NS" \
  -f infra/k8s/environments/prod/library-values.yaml \
  --set image.tag="${TAG}" \
  "${PULL_SECRET_SET[@]}" \
  --wait --timeout 20m
rollout_wait deployment zivo-library 20m

helm_record zivo-admin
helm upgrade --install zivo-admin ./infra/k8s/charts/admin -n "$NS" \
  -f infra/k8s/environments/prod/admin-values.yaml \
  --set image.tag="${TAG}" \
  "${PULL_SECRET_SET[@]}" \
  --wait --timeout 20m
rollout_wait deployment zivo-admin 20m

echo "Removing health-only zivo-api if present"
helm uninstall zivo-api -n "$NS" || true
kubectl -n "$NS" delete ingress zivo-api --ignore-not-found=true
kubectl -n "$NS" delete deployment zivo-api --ignore-not-found=true
kubectl -n "$NS" delete service zivo-api --ignore-not-found=true

helm_record zivo-web
helm upgrade --install zivo-web ./infra/k8s/charts/web -n "$NS" \
  -f infra/k8s/environments/prod/web-values.yaml \
  --set image.tag="${TAG}" \
  "${PULL_SECRET_SET[@]}" \
  --wait --timeout 20m
rollout_wait deployment zivo-web 20m

kubectl -n "$NS" get pods -o wide
echo "Done: ${TAG}"
