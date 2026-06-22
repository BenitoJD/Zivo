#!/usr/bin/env bash
# Build images on the VPS and roll out to K3s (no GHCR push required).
# Run on the VPS: bash /opt/zivo/scripts/deploy-vps-local-build.sh [tag]
set -euo pipefail

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
TAG="${1:-Zivo_0.1.$(date +%Y%m%d%H%M)}"
REPO="${REPO:-/opt/zivo}"
OWNER="${OWNER:-benitojd}"
API_IMAGE="ghcr.io/${OWNER}/zivo-api:${TAG}"
WEB_IMAGE="ghcr.io/${OWNER}/zivo-web:${TAG}"

cd "$REPO"
git fetch origin main
git reset --hard origin/main
echo "Deploying $(git rev-parse --short HEAD) as ${TAG}"

docker build --pull -t "${API_IMAGE}" -f backend/Dockerfile --target runtime backend/
docker build --pull -t "${WEB_IMAGE}" -f frontend/Dockerfile --target runtime \
  --build-arg NEXT_PUBLIC_API_URL=https://api.zivo.fyi frontend/

docker save "${API_IMAGE}" | k3s ctr images import -
docker save "${WEB_IMAGE}" | k3s ctr images import -

kubectl -n zivo delete job alembic-migrate --ignore-not-found=true
helm upgrade --install db-schema ./infra/k8s/charts/db-schema -n zivo \
  -f infra/k8s/environments/prod/backend-release-values.yaml \
  --set image.tag="${TAG}" \
  --set image.repository="ghcr.io/${OWNER}/zivo-api" \
  --set namespace=zivo \
  --set jobName=alembic-migrate \
  --wait --timeout 15m

for release in zivo-worker-io zivo-worker-cpu; do
  io=true cpu=false
  if [[ "$release" == *cpu* ]]; then io=false; cpu=true; fi
  helm upgrade --install "$release" ./infra/k8s/charts/worker -n zivo \
    -f infra/k8s/environments/prod/worker-values.yaml \
    --set image.tag="${TAG}" \
    --set "workloads.io.enabled=${io}" \
    --set "workloads.cpu.enabled=${cpu}" \
    --wait --timeout 10m
done

helm upgrade --install zivo-api ./infra/k8s/charts/api -n zivo \
  -f infra/k8s/environments/prod/backend-release-values.yaml \
  -f infra/k8s/environments/prod/api-values.yaml \
  --set image.tag="${TAG}" --wait --timeout 10m

helm upgrade --install zivo-web ./infra/k8s/charts/web -n zivo \
  -f infra/k8s/environments/prod/web-values.yaml \
  --set image.tag="${TAG}" --wait --timeout 10m

kubectl -n zivo get pods -o wide
echo "Done: ${TAG}"
