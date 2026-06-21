#!/usr/bin/env bash
# Test Alembic upgrade head, downgrade -1, upgrade head on a throwaway database.
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

createdb -h localhost -p 5455 -U zivo "${TEST_DB}" 2>/dev/null || createdb -h 127.0.0.1 -p 5455 -U zivo "${TEST_DB}"

cleanup() {
  dropdb -h localhost -p 5455 -U zivo "${TEST_DB}" --if-exists 2>/dev/null \
    || dropdb -h 127.0.0.1 -p 5455 -U zivo "${TEST_DB}" --if-exists 2>/dev/null \
    || true
}
trap cleanup EXIT

cd "${BACKEND}"
echo "[alembic-test] upgrade head"
"${PY}" -m alembic upgrade head

echo "[alembic-test] downgrade 002_qb_schema"
"${PY}" -m alembic downgrade 002_qb_schema

echo "[alembic-test] downgrade 001_intel_foundation"
"${PY}" -m alembic downgrade 001_intel_foundation

echo "[alembic-test] downgrade base"
"${PY}" -m alembic downgrade base

echo "[alembic-test] upgrade head (again)"
"${PY}" -m alembic upgrade head

echo "[alembic-test] OK"
