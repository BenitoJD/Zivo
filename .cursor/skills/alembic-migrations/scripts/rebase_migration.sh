#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
VERSIONS_DIR="${BACKEND_DIR}/alembic/versions"
DEFAULT_BASE_REF="origin/main"

log() {
  echo "[alembic-rebase] $*"
}

die() {
  echo "[alembic-rebase] ERROR: $*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage:
  $(basename "$0") "migration_message" [--base-ref <git-ref>] [--dry-run]

Examples:
  $(basename "$0") "add user flags"
  $(basename "$0") "add user flags" --dry-run
  $(basename "$0") "add user flags" --base-ref origin/main
EOF
}

add_unique_file() {
  local candidate="$1"
  local existing
  for existing in "${FILES_TO_DELETE[@]:-}"; do
    if [[ "$existing" == "$candidate" ]]; then
      return
    fi
  done
  FILES_TO_DELETE+=("$candidate")
}

collect_candidate_file() {
  local rel_path="$1"
  local abs_path=""

  [[ -n "$rel_path" ]] || return
  [[ "$rel_path" == *.py ]] || return
  [[ "$rel_path" == *"/__init__.py" ]] && return
  [[ "$rel_path" == *"/__pycache__/"* ]] && return

  abs_path="${ROOT_DIR}/${rel_path}"
  [[ -f "$abs_path" ]] || return
  add_unique_file "$abs_path"
}

MIGRATION_MESSAGE=""
BASE_REF="$DEFAULT_BASE_REF"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-ref)
      [[ $# -ge 2 ]] || die "--base-ref requires a value"
      BASE_REF="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -z "$MIGRATION_MESSAGE" ]]; then
        MIGRATION_MESSAGE="$1"
      else
        die "Unexpected extra argument: $1"
      fi
      shift
      ;;
  esac
done

[[ -n "$MIGRATION_MESSAGE" ]] || die "Migration message is required. See --help."
[[ -d "$BACKEND_DIR" ]] || die "backend directory not found at ${BACKEND_DIR}"
[[ -d "$VERSIONS_DIR" ]] || die "versions directory not found at ${VERSIONS_DIR}"

command -v git >/dev/null 2>&1 || die "git is required"
command -v alembic >/dev/null 2>&1 || log "alembic not on PATH yet; will rely on backend venv/setup_env"

cd "$ROOT_DIR"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  die "Run inside a git repository."
fi

if ! git rev-parse --verify "$BASE_REF" >/dev/null 2>&1; then
  log "Base ref ${BASE_REF} not found locally. Fetching origin/main..."
  git fetch origin main
fi

git rev-parse --verify "$BASE_REF" >/dev/null 2>&1 || die "Base ref ${BASE_REF} is unavailable."

MERGE_BASE="$(git merge-base HEAD "$BASE_REF")"
[[ -n "$MERGE_BASE" ]] || die "Unable to compute merge-base with ${BASE_REF}"

log "Using merge-base ${MERGE_BASE} against ${BASE_REF}"

FILES_TO_DELETE=()

while IFS= read -r line; do
  collect_candidate_file "$line"
done < <(git diff --name-only "${MERGE_BASE}..HEAD" -- backend/alembic/versions || true)

while IFS= read -r line; do
  collect_candidate_file "$line"
done < <(git ls-files --others --exclude-standard -- backend/alembic/versions || true)

if [[ "${#FILES_TO_DELETE[@]}" -eq 0 ]]; then
  die "No branch migration files found to delete in backend/alembic/versions."
fi

log "Migration files selected for deletion:"
for file in "${FILES_TO_DELETE[@]}"; do
  log " - ${file#${ROOT_DIR}/}"
done

if [[ "$DRY_RUN" -eq 1 ]]; then
  log "Dry run enabled; stopping before file deletion."
  exit 0
fi

for file in "${FILES_TO_DELETE[@]}"; do
  rm -f "$file"
done
log "Deleted ${#FILES_TO_DELETE[@]} migration file(s)."

cd "$BACKEND_DIR"

if [[ -f "venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "venv/bin/activate"
fi

if [[ -f "dev.sh env loader" ]]; then
  # shellcheck disable=SC1091
  source "dev.sh env loader"
fi

[[ -n "${DATABASE_URL:-}" ]] || die "DATABASE_URL is not set. Check backend/env.local.defaults, backend/.env.local, or process env."

PY_BIN="python"
if command -v python3 >/dev/null 2>&1; then
  PY_BIN="python3"
fi

log "Dropping and recreating database from DATABASE_URL..."
"$PY_BIN" - <<'PY'
import os
from urllib.parse import urlparse, urlunparse
import psycopg2

database_url = os.environ["DATABASE_URL"]
parsed = urlparse(database_url)
db_name = parsed.path.lstrip("/")

if not db_name:
    raise SystemExit("DATABASE_URL is missing a database name.")

admin_url = urlunparse(parsed._replace(path="/postgres"))

conn = psycopg2.connect(admin_url)
conn.autocommit = True
conn.set_session(autocommit=True)
cur = conn.cursor()
try:
    cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s", (db_name,))
    cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
    cur.execute(f'CREATE DATABASE "{db_name}"')
finally:
    cur.close()
    conn.close()
PY

log "Running alembic upgrade head..."
alembic upgrade head

log "Generating migration: ${MIGRATION_MESSAGE}"
alembic revision --autogenerate -m "${MIGRATION_MESSAGE}"

log "Running migration verification script..."
"${BACKEND_DIR}/scripts/test_alembic_migrations.sh"

log "Migration rebase flow completed."
