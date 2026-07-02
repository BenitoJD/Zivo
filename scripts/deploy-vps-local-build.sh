#!/usr/bin/env bash
# Build images on the VPS and roll out to K3s (no GHCR push required).
# Set PREBUILT=1 to skip docker build and use images already in containerd.
# Run on the VPS: bash /opt/zivo/scripts/deploy-vps-local-build.sh [tag]
set -euo pipefail

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
TAG="${1:-Zivo_0.1.$(date +%Y%m%d%H%M)}"
REPO="${REPO:-/opt/zivo}"
OWNER="${OWNER:-benitojd}"
API_IMAGE="ghcr.io/${OWNER}/zivo-api:${TAG}"
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
  ROLLBACK_RELEASES+=("${release}:${rev}")
}

rollout_wait() {
  local kind=$1 name=$2 timeout=${3:-10m}
  kubectl -n "$NS" rollout status "$kind/$name" --timeout="$timeout"
}

cd "$REPO"
git fetch origin main
git reset --hard origin/main
echo "Deploying $(git rev-parse --short HEAD) as ${TAG}"

if [[ "${PREBUILT:-0}" != "1" ]]; then
  docker build --pull -t "${API_IMAGE}" -f backend/Dockerfile --target runtime backend/
  docker build --pull -t "${WEB_IMAGE}" -f frontend/Dockerfile --target runtime \
    --build-arg NEXT_PUBLIC_API_URL= \
    --build-arg API_PROXY_URL=http://zivo-api:8000 \
    frontend/
  docker save "${API_IMAGE}" | sudo k3s ctr images import -
  docker save "${WEB_IMAGE}" | sudo k3s ctr images import -
else
  echo "PREBUILT=1 — pulling ${API_IMAGE} and ${WEB_IMAGE}"
  docker pull "${API_IMAGE}"
  docker pull "${WEB_IMAGE}"
  docker save "${API_IMAGE}" | sudo k3s ctr images import -
  docker save "${WEB_IMAGE}" | sudo k3s ctr images import -
fi

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

# Note: code execution (Judge0) runs on a dedicated box, not in this cluster — see
# infra/judge0/. The API reaches it via JUDGE0_URL (set in the api chart values).

for release in zivo-worker-io zivo-worker-cpu; do
  helm_record "$release"
  io=true cpu=false
  if [[ "$release" == *cpu* ]]; then io=false; cpu=true; fi
  helm upgrade --install "$release" ./infra/k8s/charts/worker -n "$NS" \
    -f infra/k8s/environments/prod/worker-values.yaml \
    --set image.tag="${TAG}" \
    --set "workloads.io.enabled=${io}" \
    --set "workloads.cpu.enabled=${cpu}" \
    --wait --timeout 10m
  rollout_wait deployment "${release#zivo-}" 10m
done

helm_record zivo-api
helm upgrade --install zivo-api ./infra/k8s/charts/api -n "$NS" \
  -f infra/k8s/environments/prod/backend-release-values.yaml \
  -f infra/k8s/environments/prod/api-values.yaml \
  --set image.tag="${TAG}" --wait --timeout 10m
rollout_wait deployment zivo-api 10m

helm_record zivo-web
helm upgrade --install zivo-web ./infra/k8s/charts/web -n "$NS" \
  -f infra/k8s/environments/prod/web-values.yaml \
  --set image.tag="${TAG}" --wait --timeout 10m
rollout_wait deployment zivo-web 10m

# Additive migrations only — run after new API/workers are live.
kubectl -n "$NS" delete job alembic-migrate --ignore-not-found=true
helm upgrade --install db-schema ./infra/k8s/charts/db-schema -n "$NS" \
  -f infra/k8s/environments/prod/backend-release-values.yaml \
  --set image.tag="${TAG}" \
  --set image.repository="ghcr.io/${OWNER}/zivo-api" \
  --set namespace="$NS" \
  --set jobName=alembic-migrate \
  --wait --timeout 15m

kubectl -n "$NS" get pods -o wide
echo "Done: ${TAG}"
