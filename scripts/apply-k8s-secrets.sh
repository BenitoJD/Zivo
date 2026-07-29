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
  --from-literal=CORS_ORIGINS="${CORS_ORIGINS:-}" \
  --from-literal=TRUSTED_PROXY_IPS="${TRUSTED_PROXY_IPS:-}" \
  --from-literal=SECRET_KEY="${SECRET_KEY:-}" \
  --from-literal=CSRF_SECRET="${CSRF_SECRET:-}" \
  --from-literal=LITELLM_MODEL="${LITELLM_MODEL:-openai/deepseek-v4-flash}" \
  --from-literal=LLM_POOL_ENABLED="${LLM_POOL_ENABLED:-true}" \
  --from-literal=DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}" \
  --from-literal=DEEPSEEK_API_BASE="${DEEPSEEK_API_BASE:-https://api.deepseek.com/v1}" \
  --from-literal=ZAI_API_KEY="${ZAI_API_KEY:-}" \
  --from-literal=ZAI_API_BASE="${ZAI_API_BASE:-}" \
  --from-literal=STEPFUN_API_KEY="${STEPFUN_API_KEY:-}" \
  --from-literal=STEPFUN_API_BASE="${STEPFUN_API_BASE:-}" \
  --from-literal=STEPFUN_ENABLED="${STEPFUN_ENABLED:-false}" \
  --from-literal=OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}" \
  --from-literal=OPENROUTER_API_BASE="${OPENROUTER_API_BASE:-}" \
  --from-literal=OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
  --from-literal=OPENAI_API_BASE="${OPENAI_API_BASE:-}" \
  --from-literal=TELEGRAM_API_ID="${TELEGRAM_API_ID:-}" \
  --from-literal=TELEGRAM_API_HASH="${TELEGRAM_API_HASH:-}" \
  --from-literal=TELEGRAM_SESSION="${TELEGRAM_SESSION:-}"
echo "zivo-secrets updated"
