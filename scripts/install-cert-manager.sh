#!/usr/bin/env bash
set -euo pipefail
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
if ! kubectl get namespace cert-manager &>/dev/null; then
  kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.4/cert-manager.yaml
  kubectl wait --for=condition=Available deployment/cert-manager -n cert-manager --timeout=300s
fi
kubectl apply -f "$(dirname "$0")/../infra/k8s/cert-manager/cluster-issuer.yaml"
