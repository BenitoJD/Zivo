#!/usr/bin/env bash
set -euo pipefail
SECRETS_FILE="${1:-/root/.zivo/secrets.env}"
# shellcheck disable=SC1090
source "$SECRETS_FILE"
kubectl -n zivo delete secret zivo-secrets --ignore-not-found=true
kubectl -n zivo create secret generic zivo-secrets \
  --from-literal=POSTGRES_USER="$POSTGRES_USER" \
  --from-literal=POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  --from-literal=DATABASE_URL="$DATABASE_URL" \
  --from-literal=MINIO_ROOT_USER="$MINIO_ROOT_USER" \
  --from-literal=MINIO_ROOT_PASSWORD="$MINIO_ROOT_PASSWORD" \
  --from-literal=MINIO_ACCESS_KEY="$MINIO_ACCESS_KEY" \
  --from-literal=MINIO_SECRET_KEY="$MINIO_SECRET_KEY" \
  --from-literal=MINIO_ENDPOINT="$MINIO_ENDPOINT" \
  --from-literal=MINIO_PUBLIC_ENDPOINT="${MINIO_PUBLIC_ENDPOINT:-}" \
  --from-literal=MINIO_PUBLIC_SECURE="${MINIO_PUBLIC_SECURE:-}" \
  --from-literal=MINIO_BUCKET="$MINIO_BUCKET" \
  --from-literal=MINIO_SECURE="${MINIO_SECURE:-false}" \
  --from-literal=APP_ENV="${APP_ENV:-production}" \
  --from-literal=CORS_ORIGINS="${CORS_ORIGINS:-}"
echo "zivo-secrets updated"
