#!/usr/bin/env bash
# Install the app platform on the HA Kubernetes cluster (kubeadm + Cilium, 3 masters
# + 3 workers + 1 HAProxy LB): default storage class, Traefik ingress, cert-manager,
# and the zivo data layer (Postgres + MinIO). Idempotent; re-run any time.
#
# Prereqs: kubectl + helm on this machine, KUBECONFIG pointing at the cluster
#          (admin.conf from master-1; its server already targets the LB :8443).
# Usage:   KUBECONFIG=~/.kube/zivo-ha.conf ./scripts/install-ha-platform.sh
#
# The k8s *cluster* itself is built by infra/ansible (site.yml). This script is the
# app-layer equivalent; deploy-vps-local-build.sh stays the CI path for releases.
set -euo pipefail
: "${KUBECONFIG:?export KUBECONFIG to the HA cluster admin kubeconfig first}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TRAEFIK_VALUES=(
  --set deployment.kind=DaemonSet
  --set hostNetwork=true
  --set deployment.dnsPolicy=ClusterFirstWithHostNet
  --set updateStrategy.rollingUpdate.maxUnavailable=1
  --set updateStrategy.rollingUpdate.maxSurge=0
  --set service.type=ClusterIP
  --set ingressRoute.dashboard.enabled=false
  --set ports.web.port=80
  --set ports.websecure.port=443
  --set podSecurityContext.runAsUser=0
  --set podSecurityContext.runAsGroup=0
  --set podSecurityContext.runAsNonRoot=false
  --set 'securityContext.capabilities.add={NET_BIND_SERVICE}'
)

echo "==> label workers (Traefik hostNetwork targets)"
for n in $(kubectl get nodes -l '!node-role.kubernetes.io/control-plane' -o name); do
  kubectl label "$n" node.zivo/role=worker --overwrite >/dev/null
done

echo "==> default storage class: local-path (Immediate; the Postgres backup PVC has"
echo "    no pod consumer, so WaitForFirstConsumer would never bind it)"
if ! kubectl get storageclass local-path >/dev/null 2>&1; then
  kubectl apply -f https://raw.githubusercontent.com/rancher/local-path-provisioner/v0.0.31/deploy/local-path-storage.yaml
  cat <<'EOF' | kubectl apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: local-path
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: rancher.io/local-path
reclaimPolicy: Delete
volumeBindingMode: Immediate
allowVolumeExpansion: false
EOF
  kubectl delete storageclass local-path --wait=false >/dev/null 2>&1 || true
fi
kubectl get storageclass local-path -o name

echo "==> Traefik ingress (hostNetwork DaemonSet on workers; ports 80/443; root +"
echo "    NET_BIND_SERVICE (the chart's uid-65532 default cannot bind :443 in host net)"
helm repo add traefik https://traefik.github.io/charts >/dev/null 2>&1 || true
helm repo update >/dev/null
helm upgrade --install traefik traefik/traefik -n kube-system "${TRAEFIK_VALUES[@]}" --wait --timeout 300s
# hostNetwork preserves the real client IP (the old K3s traefik-realip.yaml overlay
# solved SNAT for ServiceLB; hostNetwork needs no equivalent).

echo "==> cert-manager + letsencrypt-prod ClusterIssuer"
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.4/cert-manager.yaml
kubectl -n cert-manager wait --for=condition=Available deployment --all --timeout=300s
kubectl apply -f "$REPO/infra/k8s/cert-manager/cluster-issuer.yaml"

echo "==> zivo namespace + Postgres + MinIO"
kubectl create namespace zivo --dry-run=client -o yaml | kubectl apply -f -
helm upgrade --install zivo-postgres "$REPO/infra/k8s/charts/postgres" -n zivo \
  -f "$REPO/infra/k8s/environments/prod/postgres-values.yaml" --wait --timeout 600s
# MinIO ingress stays HTTP until s3.zivo.fyi's DNS points at a worker node (cert-manager
# must not park an ACME solver on a host that still resolves to the old VPS).
helm upgrade --install zivo-minio "$REPO/infra/k8s/charts/minio" -n zivo \
  -f "$REPO/infra/k8s/environments/prod/minio-values.yaml" \
  --set ingress.tls=false --set ingress.entrypoint=web --set publicUrl=http://s3.zivo.fyi \
  --wait --timeout 600s
# The Postgres backup PVC has no pod consumer; pin it to the Postgres node so
# local-path can provision it in Immediate mode.
if [[ "$(kubectl -n zivo get pvc postgres-backup -o jsonpath='{.status.phase}')" != "Bound" ]]; then
  kubectl -n zivo delete pvc postgres-backup --ignore-not-found --wait=false
  node=$(kubectl -n zivo get pod postgres-0 -o jsonpath='{.spec.nodeName}')
  cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-backup
  namespace: zivo
  annotations:
    volume.alpha.kubernetes.io/selected-node: $node
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: local-path
  resources:
    requests:
      storage: 10Gi
EOF
fi

echo "==> zivo-secrets"
echo "    Not done by this script: create it with scripts/apply-k8s-secrets.sh"
echo "    against a secrets.env (a generated copy lives on master-1: /root/.zivo/secrets.env)."

kubectl -n zivo get pods,pvc
echo "Platform ready."
