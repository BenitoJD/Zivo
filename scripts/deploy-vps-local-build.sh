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
  # Push to GHCR; prod k3s pulls these (images are public, unique tag per deploy).
  docker push "${API_IMAGE}"
  docker push "${WEB_IMAGE}"
else
  echo "PREBUILT=1 — ${API_IMAGE} and ${WEB_IMAGE} assumed already in GHCR"
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
