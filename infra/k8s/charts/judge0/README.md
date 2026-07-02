# Judge0 (hardened self-host)

Self-hosted [Judge0](https://github.com/judge0/judge0) code-execution sandbox for Zivo's
interview coding rounds. Runs in its own `judge0` namespace with `judge0/judge0:1.13.1`
(server on :2358 + privileged workers) backed by an in-namespace Postgres + Redis.

The app reaches it **only** as `http://judge0-server.judge0.svc:2358` (set on the API via
`JUDGE0_URL`). Client code lives in `backend/app/services/code_execution.py`.

## ⚠️ Security reality
Judge0 workers execute **untrusted user code** and therefore **require `privileged: true`**
(the `isolate` sandbox needs cgroups/namespaces). Privilege can't be removed — so we contain
the blast radius at the network layer (`templates/networkpolicy.yaml`):

- default-deny all ingress + egress in the namespace
- egress allowed only to in-namespace Postgres/Redis + kube-dns
- **no egress to the `zivo` namespace, the Zivo DB, or the public internet**
- the only ingress is `zivo` namespace → `judge0-server:2358`
- `ENABLE_NETWORK=false` so sandboxed programs get no network at all

NetworkPolicies require a CNI that enforces them. **K3s ships Flannel, which does NOT enforce
NetworkPolicy** — verify your cluster runs a policy-enforcing CNI (Calico/Cilium) or the
egress hardening is a no-op. This is the single most important thing to confirm before trusting it.

## cgroups / K3s caveat
Judge0 1.13 + `isolate` historically need **cgroup v1**. On a cgroup-v2 host the workers may
fail to run submissions. If `judge0-workers` is crashlooping or every submission errors, boot
the node with `systemd.unified_cgroup_hierarchy=0` (or use Judge0's cgroup-v2 guidance). This
can only be validated on the actual node at deploy time.

## Deploy
Credentials are **not** in git (`createSecret: false` in prod). Create the secret once:

```bash
kubectl create namespace judge0 --dry-run=client -o yaml | kubectl apply -f -
kubectl -n judge0 create secret generic judge0-secrets \
  --from-literal=POSTGRES_USER=judge0 \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 24)" \
  --from-literal=POSTGRES_DB=judge0 \
  --from-literal=REDIS_PASSWORD="$(openssl rand -hex 24)"
```

Then the normal **Deploy Zivo** GitHub Action installs the chart (see
`scripts/deploy-vps-local-build.sh`), or manually:

```bash
helm upgrade --install judge0 ./infra/k8s/charts/judge0 -n zivo \
  -f infra/k8s/environments/prod/judge0-values.yaml --wait --timeout 10m
```

## Smoke tests (run on-cluster after deploy)
```bash
# 1. sandbox works
kubectl -n zivo run j0-check --rm -it --image=curlimages/curl --restart=Never -- \
  sh -c 'curl -s -X POST "http://judge0-server.judge0.svc:2358/submissions?base64_encoded=false&wait=true" \
    -H "Content-Type: application/json" \
    -d "{\"language_id\":71,\"source_code\":\"print(6*7)\"}"'
# expect: {"stdout":"42\n","status":{"id":3,"description":"Accepted"}, ...}

# 2. hardening: a judge0 pod must NOT reach the Zivo Postgres (should hang/fail)
kubectl -n judge0 exec deploy/judge0-workers -- \
  sh -c 'timeout 5 sh -c "echo > /dev/tcp/postgres.zivo.svc/5432" && echo REACHABLE || echo blocked'
# expect: blocked
```

## Local dev
`docker compose up -d judge0-db judge0-redis judge0-server judge0-workers` → API on
`localhost:2358` (already the `judge0_url` default in `backend/app/config.py`).
