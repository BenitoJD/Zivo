#!/usr/bin/env bash
set -euo pipefail
TAG="${1:?usage: promote-prod-image-tags.sh <tag> <ghcr-owner>}"
OWNER="${2:?usage: promote-prod-image-tags.sh <tag> <ghcr-owner>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROD="${ROOT}/infra/k8s/environments/prod"
API_IMAGE="ghcr.io/${OWNER}/zivo-api"
AUTH_IMAGE="ghcr.io/${OWNER}/zivo-auth"
WEB_IMAGE="ghcr.io/${OWNER}/zivo-web"
for file in backend-release-values.yaml api-values.yaml; do
  yq -i ".image.tag = \"${TAG}\"" "${PROD}/${file}"
  yq -i ".image.repository = \"${API_IMAGE}\"" "${PROD}/${file}"
done
yq -i ".image.tag = \"${TAG}\"" "${PROD}/auth-values.yaml"
yq -i ".image.repository = \"${AUTH_IMAGE}\"" "${PROD}/auth-values.yaml"
yq -i ".image.tag = \"${TAG}\"" "${PROD}/web-values.yaml"
yq -i ".image.repository = \"${WEB_IMAGE}\"" "${PROD}/web-values.yaml"
