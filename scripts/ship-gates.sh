#!/usr/bin/env bash
# Pre-commit / pre-deploy quality gates — mirrors CI plus frontend lint.
# See agents/ship-gates.md.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SCOPE="${1:-all}"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "${HOME}/.venv/zivo/bin/python" ]]; then
    PYTHON_BIN="${HOME}/.venv/zivo/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    PYTHON_BIN="python"
  fi
fi

run_backend() {
  echo "==> backend: ruff"
  (cd backend && "$PYTHON_BIN" -m ruff check .)

  echo "==> backend: import check"
  (cd backend && "$PYTHON_BIN" -c "from app.main import app; assert app.title == 'Zivo API'")

  echo "==> worker: import smoke (no app.api or HTTP service packages)"
  (cd backend && "$PYTHON_BIN" -c "
from app.eta.worker import run_eta_worker
from app.eta.worker_async import run_eta_worker_async
import sys
pkgs = {'practice_api', 'content_api', 'study_api', 'library_api', 'admin_api'}
loaded = [m for m in sys.modules if m == 'app.api' or m.startswith('app.api.') or m.split('.')[0] in pkgs]
assert not loaded, loaded
assert callable(run_eta_worker) and callable(run_eta_worker_async)
")

  echo "==> backend: unit tests"
  (cd backend && "$PYTHON_BIN" -m pytest tests/unit/ -q --tb=no)
}

run_auth() {
  echo "==> auth: ruff"
  (cd auth && "$PYTHON_BIN" -m ruff check .)

  echo "==> auth: import check"
  (cd auth && "$PYTHON_BIN" -c "from app.main import app; assert app.title == 'Zivo Auth'")

  echo "==> auth: unit tests"
  (cd auth && "$PYTHON_BIN" -m pytest tests/unit/ -q --tb=no)
}

run_storage() {
  echo "==> storage: ruff"
  (cd storage && "$PYTHON_BIN" -m ruff check .)

  echo "==> storage: import check"
  (cd storage && "$PYTHON_BIN" -c "from app.main import app; assert app.title == 'Zivo Storage'")

  echo "==> storage: unit tests"
  (cd storage && "$PYTHON_BIN" -m pytest tests/unit/ -q --tb=no)
}

run_product_service() {
  local name="$1"
  local title="$2"
  local module="$3"
  local own_pkg="${name}_api"
  echo "==> ${name}: ruff"
  (cd "$name" && "$PYTHON_BIN" -m ruff check .)

  echo "==> ${name}: import check"
  (cd "$name" && PYTHONPATH="../backend:." "$PYTHON_BIN" -c "from ${module} import app; import sys; siblings=[m for m in sys.modules if m.split('.')[0] in {'practice_api','content_api','study_api','library_api','admin_api'} - {'${own_pkg}'}]; assert not siblings, siblings; assert app.title == '${title}'")

  echo "==> ${name}: unit tests"
  (cd "$name" && PYTHONPATH="../backend:." "$PYTHON_BIN" -m pytest tests/unit/ -q --tb=no)
}

run_practice() {
  run_product_service practice "Zivo Practice" practice_main
}

run_content() {
  run_product_service content "Zivo Content" content_main
}

run_study() {
  run_product_service study "Zivo Study" study_main
}

run_library() {
  run_product_service library "Zivo Library" library_main
}

run_admin() {
  run_product_service admin "Zivo Admin" admin_main
}

run_frontend() {
  echo "==> frontend: eslint"
  (cd frontend && npm run lint)

  echo "==> frontend: build"
  (cd frontend && npm run build)
}

case "$SCOPE" in
  all)
    run_backend
    run_auth
    run_storage
    run_practice
    run_content
    run_study
    run_library
    run_admin
    run_frontend
    ;;
  backend)
    run_backend
    ;;
  auth)
    run_auth
    ;;
  storage)
    run_storage
    ;;
  practice)
    run_practice
    ;;
  content)
    run_content
    ;;
  study)
    run_study
    ;;
  library)
    run_library
    ;;
  admin)
    run_admin
    ;;
  frontend)
    run_frontend
    ;;
  *)
    echo "Usage: $0 [all|backend|auth|storage|practice|content|study|library|admin|frontend]" >&2
    exit 2
    ;;
esac

echo "Ship gates passed ($SCOPE)."
