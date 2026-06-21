#!/usr/bin/env bash
# Test Alembic upgrade head, downgrade to base, upgrade head on a throwaway database.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND="${ROOT}/backend"
VENV="${HOME}/.venv/zivo"

if [[ ! -x "${VENV}/bin/python" ]]; then
  echo "Missing ${VENV}. Run ./scripts/dev.sh setup first." >&2
  exit 1
fi

PY="${VENV}/bin/python"
BASE_URL="${DATABASE_URL:-postgresql+psycopg://zivo:zivo@localhost:5455/zivo}"
TEST_DB="zivo_alembic_test_$$"
export DATABASE_URL="${BASE_URL%/*}/${TEST_DB}"

echo "[alembic-test] Using ${DATABASE_URL}"

"${PY}" - <<'PY'
import os
import re
import sys

import psycopg

base = os.environ["DATABASE_URL"]
m = re.match(r"^(postgresql(?:\+\w+)?://)([^/]+)(/.*)?$", base)
if not m:
    sys.exit("Invalid DATABASE_URL")
prefix, hostpart, dbpath = m.group(1), m.group(2), m.group(3) or ""
test_db = dbpath.lstrip("/")
admin_url = f"{prefix}{hostpart}/postgres".replace("postgresql+psycopg://", "postgresql://")

with psycopg.connect(admin_url) as conn:
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (test_db,))
        if cur.fetchone():
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (test_db,),
            )
            cur.execute(f'DROP DATABASE "{test_db}"')
        cur.execute(f'CREATE DATABASE "{test_db}"')
PY

cleanup() {
  "${PY}" - <<'PY' || true
import os
import re
import psycopg

base = os.environ["DATABASE_URL"]
m = re.match(r"^(postgresql(?:\+\w+)?://)([^/]+)(/.*)?$", base)
prefix, hostpart, dbpath = m.group(1), m.group(2), m.group(3) or ""
test_db = dbpath.lstrip("/")
admin_url = f"{prefix}{hostpart}/postgres".replace("postgresql+psycopg://", "postgresql://")

with psycopg.connect(admin_url) as conn:
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
            (test_db,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{test_db}"')
PY
}
trap cleanup EXIT

cd "${BACKEND}"
echo "[alembic-test] upgrade head"
"${PY}" -m alembic upgrade head

echo "[alembic-test] downgrade base"
"${PY}" -m alembic downgrade base

echo "[alembic-test] upgrade head (again)"
"${PY}" -m alembic upgrade head

echo "[alembic-test] OK"
