#!/usr/bin/env bash
set -euo pipefail
SECRETS_FILE="${1:-/root/.zivo/secrets.env}"
# shellcheck disable=SC1090
source "$SECRETS_FILE"
kubectl -n zivo delete secret zivo-secrets --ignore-not-found=true

# LITELLM_MODEL is the DEFAULT chat model: sync_default_chat_model_from_env pins it
# at every boot, overriding the DeepSeek-first precedence hardcoded in
# ensure_registry_providers. Switch providers HERE (or in secrets.env) — never by
# editing is_default in the DB, which the next pod restart reverts.
# LLM_MAX_CONCURRENT is the process-wide cap across ALL providers. Per-provider
# ceilings are data now — qb.llm_providers.max_concurrency (Step Fun = 8, which
# hard-rejects the 9th concurrent call) — so change a provider's limit in the
# registry, not here.
# NOTE: no inline comments inside the command below — a '#' on a backslash-continued
# line comments out the rest of THAT line including the '\', silently truncating
# the secret and dropping every key after it.
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
  --from-literal=CORS_ORIGINS="${CORS_ORIGINS:-https://zivo.fyi,https://www.zivo.fyi}" \
  --from-literal=TRUSTED_PROXY_IPS="${TRUSTED_PROXY_IPS:-}" \
  --from-literal=SECRET_KEY="${SECRET_KEY:-}" \
  --from-literal=CSRF_SECRET="${CSRF_SECRET:-}" \
  --from-literal=LITELLM_MODEL="${LITELLM_MODEL:-openai/step-3.5-flash}" \
  --from-literal=LLM_POOL_ENABLED="${LLM_POOL_ENABLED:-true}" \
  --from-literal=LLM_MAX_CONCURRENT="${LLM_MAX_CONCURRENT:-8}" \
  --from-literal=LLM_GLOBAL_SLOT_WAIT="${LLM_GLOBAL_SLOT_WAIT:-150}" \
  --from-literal=DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}" \
  --from-literal=DEEPSEEK_API_BASE="${DEEPSEEK_API_BASE:-https://api.deepseek.com/v1}" \
  --from-literal=ZAI_API_KEY="${ZAI_API_KEY:-}" \
  --from-literal=ZAI_API_BASE="${ZAI_API_BASE:-}" \
  --from-literal=ZAI_ENABLED="${ZAI_ENABLED:-true}" \
  --from-literal=STEPFUN_API_KEY="${STEPFUN_API_KEY:-}" \
  --from-literal=STEPFUN_API_BASE="${STEPFUN_API_BASE:-}" \
  --from-literal=STEPFUN_ENABLED="${STEPFUN_ENABLED:-false}" \
  --from-literal=OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}" \
  --from-literal=OPENROUTER_API_BASE="${OPENROUTER_API_BASE:-}" \
  --from-literal=OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
  --from-literal=OPENAI_API_BASE="${OPENAI_API_BASE:-}" \
  --from-literal=TELEGRAM_API_ID="${TELEGRAM_API_ID:-}" \
  --from-literal=TELEGRAM_API_HASH="${TELEGRAM_API_HASH:-}" \
  --from-literal=TELEGRAM_SESSION="${TELEGRAM_SESSION:-}" \
  --from-literal=GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:-}" \
  --from-literal=GOOGLE_CLIENT_SECRET="${GOOGLE_CLIENT_SECRET:-}" \
  --from-literal=GOOGLE_REDIRECT_URI="${GOOGLE_REDIRECT_URI:-https://auth.zivo.fyi/api/auth/google/callback}" \
  --from-literal=FRONTEND_URL="${FRONTEND_URL:-https://zivo.fyi}" \
  --from-literal=COOKIE_DOMAIN="${COOKIE_DOMAIN:-zivo.fyi}"
echo "zivo-secrets updated"

if [ -z "${GOOGLE_CLIENT_ID:-}" ] || [ -z "${GOOGLE_CLIENT_SECRET:-}" ]; then
  echo "NOTE: GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET are empty — /api/auth/google/status" \
       "reports enabled=false and the UI hides the 'Continue with Google' button." >&2
fi
