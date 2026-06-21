#!/usr/bin/env bash
set -euo pipefail
NAMESPACE="${NAMESPACE:-zivo}"
JOB_NAME="${JOB_NAME:-intel-schema}"
IMAGE_REPOSITORY="${IMAGE_REPOSITORY:?IMAGE_REPOSITORY is required}"
IMAGE_TAG="${IMAGE_TAG:?IMAGE_TAG is required}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-600s}"
CHART_PATH="${CHART_PATH:-./infra/k8s/charts/db-schema}"
VALUES_FILE="${VALUES_FILE:-./infra/k8s/environments/prod/backend-release-values.yaml}"

if command -v k3s >/dev/null 2>&1; then
  KUBECTL=(sudo k3s kubectl)
elif command -v kubectl >/dev/null 2>&1; then
  KUBECTL=(kubectl)
else
  echo "kubectl or k3s required" >&2
  exit 2
fi

"${KUBECTL[@]}" -n "$NAMESPACE" delete job "$JOB_NAME" --ignore-not-found=true

helm upgrade --install db-schema "$CHART_PATH" \
  -n "$NAMESPACE" --create-namespace \
  -f "$VALUES_FILE" \
  --set "image.repository=${IMAGE_REPOSITORY}" \
  --set "image.tag=${IMAGE_TAG}" \
  --set "namespace=${NAMESPACE}" \
  --set "jobName=${JOB_NAME}" \
  --wait --timeout "${WAIT_TIMEOUT}"

if ! "${KUBECTL[@]}" -n "$NAMESPACE" wait --for=condition=complete "job/${JOB_NAME}" --timeout="${WAIT_TIMEOUT}"; then
  "${KUBECTL[@]}" -n "$NAMESPACE" logs "job/${JOB_NAME}" --tail=200 || true
  exit 1
fi

echo "intel schema migration completed."
