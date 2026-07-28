#!/usr/bin/env bash
# Bootstrap Zivo production VPS: k3s, helm, docker, k8s secrets, GitHub Actions runner.
set -euo pipefail

REPO="${ZIVO_REPO:-BenitoJD/Zivo}"
RUNNER_LABELS="${RUNNER_LABELS:-self-hosted,zivo,Linux,X64}"
SECRETS_DIR="/root/.zivo"
SECRETS_FILE="${SECRETS_DIR}/secrets.env"

log() { echo "[bootstrap] $*"; }

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl git jq openssl ca-certificates gnupg lsb-release

if ! command -v gh >/dev/null; then
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
  chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list
  apt-get update -qq
  apt-get install -y -qq gh
fi

if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi

# 4G swap prevents Traefik/k3s OOM when HPA briefly scales up on the 12GB VPS.
if ! swapon --show | grep -q '/swapfile'; then
  log "Enabling 4G swap..."
  fallocate -l 4G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=4096 status=progress
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

if ! command -v k3s >/dev/null; then
  log "Installing K3s..."
  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--write-kubeconfig-mode 644" sh -
fi
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

if ! command -v helm >/dev/null; then
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

if ! command -v yq >/dev/null; then
  curl -fsSL -o /usr/local/bin/yq https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64
  chmod +x /usr/local/bin/yq
fi

mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"

if [[ ! -f "$SECRETS_FILE" ]]; then
  PG_PASS=$(openssl rand -hex 24)
  MINIO_PASS=$(openssl rand -hex 24)
  cat >"$SECRETS_FILE" <<EOF
POSTGRES_USER=zivo
POSTGRES_PASSWORD=${PG_PASS}
DATABASE_URL=postgresql+psycopg://zivo:${PG_PASS}@postgres.zivo.svc:5432/zivo
MINIO_ROOT_USER=zivo
MINIO_ROOT_PASSWORD=${MINIO_PASS}
MINIO_ACCESS_KEY=zivo
MINIO_SECRET_KEY=${MINIO_PASS}
MINIO_ENDPOINT=minio.zivo.svc:9000
MINIO_PUBLIC_ENDPOINT=s3.zivo.fyi
MINIO_PUBLIC_SECURE=true
MINIO_BUCKET=zivo-artifacts
MINIO_SECURE=false
APP_ENV=production
CORS_ORIGINS=https://zivo.fyi,https://www.zivo.fyi
EOF
  chmod 600 "$SECRETS_FILE"
  log "Wrote $SECRETS_FILE"
else
  log "Reusing $SECRETS_FILE"
fi

# shellcheck disable=SC1090
source "$SECRETS_FILE"

kubectl create namespace zivo --dry-run=client -o yaml | kubectl apply -f -
bash "$(dirname "$0")/apply-k8s-secrets.sh" "$SECRETS_FILE"

# Preserve real client IPs at Traefik (rate limiting + logging key on the
# actual caller). See infra/k8s/traefik-realip.yaml for the why.
TRAEFIK_REALIP_SRC="$(dirname "$0")/../infra/k8s/traefik-realip.yaml"
if [[ -f "$TRAEFIK_REALIP_SRC" ]]; then
  cp "$TRAEFIK_REALIP_SRC" /var/lib/rancher/k3s/server/manifests/traefik-realip.yaml
fi

if [[ -f "$(dirname "$0")/install-cert-manager.sh" ]]; then
  bash "$(dirname "$0")/install-cert-manager.sh"
fi

RUNNER_DIR="/opt/actions-runner"
if [[ ! -f "$RUNNER_DIR/.runner" && -n "${RUNNER_TOKEN:-}" ]]; then
  id github-runner &>/dev/null || useradd -m -s /bin/bash github-runner
  usermod -aG docker github-runner
  mkdir -p "$RUNNER_DIR" && cd "$RUNNER_DIR"
  RUNNER_VERSION=$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest | jq -r .tag_name | sed 's/v//')
  curl -fsSLO "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
  tar xzf "actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
  chown -R github-runner:github-runner "$RUNNER_DIR"
  sudo -u github-runner ./config.sh --url "https://github.com/${REPO}" --token "$RUNNER_TOKEN" \
    --labels "$RUNNER_LABELS" --unattended --replace
  ./svc.sh install github-runner
  ./svc.sh start
  mkdir -p /home/github-runner/.kube
  cp /etc/rancher/k3s/k3s.yaml /home/github-runner/.kube/config
  chown -R github-runner:github-runner /home/github-runner/.kube
  chmod 600 /home/github-runner/.kube/config
  cat >/etc/sudoers.d/github-runner-k3s <<'SUDOERS'
github-runner ALL=(ALL) NOPASSWD: /usr/local/bin/k3s
SUDOERS
  chmod 440 /etc/sudoers.d/github-runner-k3s
fi

log "Bootstrap complete."
kubectl get nodes
