#!/usr/bin/env bash
# Point zivo-secrets DATABASE_URL at PgBouncer; keep DATABASE_URL_DIRECT on Postgres.
# Usage: ./scripts/wire-pgbouncer-database-url.sh [namespace]
set -euo pipefail

NS="${1:-zivo}"
SECRET="${SECRET:-zivo-secrets}"

if ! kubectl -n "$NS" get secret "$SECRET" >/dev/null 2>&1; then
  echo "Secret $SECRET not found in namespace $NS"
  exit 1
fi

USER=$(kubectl -n "$NS" get secret "$SECRET" -o jsonpath='{.data.POSTGRES_USER}' | base64 -d)
PASS=$(kubectl -n "$NS" get secret "$SECRET" -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)
export POOL_URL="postgresql+psycopg://${USER}:${PASS}@pgbouncer:5432/zivo"
export DIRECT_URL="postgresql+psycopg://${USER}:${PASS}@postgres:5432/zivo"

kubectl -n "$NS" patch secret "$SECRET" --type merge -p "$(python3 -c '
import base64, json, os
print(json.dumps({"data": {
  "DATABASE_URL": base64.b64encode(os.environ["POOL_URL"].encode()).decode(),
  "DATABASE_URL_DIRECT": base64.b64encode(os.environ["DIRECT_URL"].encode()).decode(),
}}))
')"

echo "Patched $SECRET: DATABASE_URL -> pgbouncer, DATABASE_URL_DIRECT -> postgres"
