#!/usr/bin/env bash
# Reclaim disk on the CI runner box (zivo-runner-1) when Docker fills it.
#
# The nightly docker-cleanup.timer normally keeps the disk in check, but a
# burst of builds can outrun it (it happened 2026-09-14: 89% full). Run this
# script by hand when df climbs:
#
#   ./scripts/prune-runner-docker.sh                # safe prune (default)
#   ./scripts/prune-runner-docker.sh --aggressive   # also drop tagged images
#   ./scripts/prune-runner-docker.sh --force        # ignore build-in-progress guard
#
# Safe prune keeps the newest KEEP_TAGS=3 tags per repository and a warm
# build-cache floor, mirroring infra/ansible/roles/gh_runner_cleanup.
# Aggressive mode removes every unused image and all build cache; nothing is
# lost because pushed images live on GHCR and can be re-pulled.
set -euo pipefail

RUNNER_HOST="${RUNNER_HOST:-zivo-runner-1}"
GUARD="/home/github-runner/.deploy-in-progress"
AGGRESSIVE=0
FORCE=0

for arg in "$@"; do
  case "$arg" in
    --aggressive) AGGRESSIVE=1 ;;
    --force) FORCE=1 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

disk_used() {
  ssh -o BatchMode=yes -o ConnectTimeout=10 "$RUNNER_HOST" \
    "df -h / | awk 'NR==2{print \$5}'"
}

# A fresh guard file means the pipeline is mid-build; pruning now would slow
# it down or corrupt layers. A stale guard (>4h) means a run died hard and
# the nightly cleanup self-heals it, so treat it as absent.
guard_is_fresh() {
  ssh -o BatchMode=yes -o ConnectTimeout=10 "$RUNNER_HOST" \
    "[ -f '$GUARD' ] && [ -n \"\$(find '$GUARD' -mmin -240 -print 2>/dev/null)\" ]"
}

if [[ $FORCE -ne 1 ]] && guard_is_fresh; then
  echo "A pipeline build is in progress on $RUNNER_HOST (guard file fresh)."
  echo "Wait for it to finish, or re-run with --force to prune anyway."
  exit 1
fi

echo "Runner: $RUNNER_HOST"
echo "Disk before: $(disk_used) used"

if ssh -o BatchMode=yes "$RUNNER_HOST" "test -x /usr/local/sbin/docker-cleanup.sh"; then
  echo "Running the managed cleanup script (keeps 3 newest tags per repo)..."
  ssh -o BatchMode=yes "$RUNNER_HOST" "/usr/local/sbin/docker-cleanup.sh"
else
  echo "Managed cleanup script not found; running a direct prune..."
  ssh -o BatchMode=yes "$RUNNER_HOST" '
    docker container prune -f
    docker image prune -f
    docker builder prune -f --keep-storage "${KEEP_BUILDER_GB:-8}GB"
    docker volume prune -f
  '
fi

if [[ $AGGRESSIVE -eq 1 ]]; then
  echo "Aggressive pass: dropping every unused image and all build cache..."
  ssh -o BatchMode=yes "$RUNNER_HOST" '
    docker image prune -af --filter "until=48h"
    docker builder prune -af
  '
fi

echo "Disk after:  $(disk_used) used"
echo "Done. Images and cache removed here are re-pullable from GHCR."
