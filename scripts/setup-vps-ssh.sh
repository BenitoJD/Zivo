#!/usr/bin/env bash
# Install local SSH public key on Zivo VPS (run once if password login is still enabled).
# Usage: VPS_ROOT_PASSWORD='...' ./scripts/setup-vps-ssh.sh
set -euo pipefail

HOST="${ZIVO_VPS_HOST:-103.194.228.47}"
USER="${ZIVO_VPS_USER:-root}"
PUBKEY_FILE="${HOME}/.ssh/id_ed25519.pub"

if [[ ! -f "$PUBKEY_FILE" ]]; then
  echo "Missing $PUBKEY_FILE — generate with: ssh-keygen -t ed25519"
  exit 1
fi

if [[ -z "${VPS_ROOT_PASSWORD:-}" ]]; then
  echo "Set VPS_ROOT_PASSWORD for the initial password login."
  exit 1
fi

PUBKEY=$(cat "$PUBKEY_FILE")

expect <<EOF
set timeout 120
spawn ssh -o StrictHostKeyChecking=accept-new -o PreferredAuthentications=password -o PubkeyAuthentication=no ${USER}@${HOST} "mkdir -p ~/.ssh && chmod 700 ~/.ssh && grep -qxF '${PUBKEY}' ~/.ssh/authorized_keys 2>/dev/null || echo '${PUBKEY}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && echo ZIVO_SSH_READY"
expect {
  "password:" { send "${VPS_ROOT_PASSWORD}\r"; exp_continue }
  "Password:" { send "${VPS_ROOT_PASSWORD}\r"; exp_continue }
  eof
}
EOF

echo "Testing key login..."
ssh -o BatchMode=yes -o ConnectTimeout=15 zivo-vps "echo OK: \$(hostname)"

echo "Done. Use: ssh zivo-vps"
