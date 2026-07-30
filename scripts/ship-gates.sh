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

  echo "==> backend: unit tests"
  (cd backend && "$PYTHON_BIN" -m pytest tests/unit/ -q --tb=no)
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
    run_frontend
    ;;
  backend)
    run_backend
    ;;
  frontend)
    run_frontend
    ;;
  *)
    echo "Usage: $0 [all|backend|frontend]" >&2
    exit 2
    ;;
esac

echo "Ship gates passed ($SCOPE)."
