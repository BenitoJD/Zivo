#!/usr/bin/env bash
# Prune Docker build cache on CI / self-hosted runners when over disk budget.
set -euo pipefail
THRESHOLD_BYTES="${CI_DOCKER_DISK_LIMIT_BYTES:-10737418240}"
if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  exit 0
fi
usage=$(docker system df --format '{{.Size}}' 2>/dev/null | head -1 || echo 0)
if [[ "${1:-}" == "--force" ]]; then
  docker builder prune -af 2>/dev/null || true
  docker image prune -af 2>/dev/null || true
fi
