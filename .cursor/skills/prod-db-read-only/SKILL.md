---
name: "prod-db-read-only"
description: "Read-only access to the zivo production PostgreSQL database on the VPS via kubectl port-forward and psql. Use when the user asks to query prod DB, check production data, investigate live issues, or analyze production records."
---

# Production Database Read-Only Access

## Connection Details

| Parameter | Value |
|-----------|-------|
| Prod host | VPS (`ssh zivo-vps`) running K3s |
| Postgres | in-cluster StatefulSet, Helm release `zivo-postgres`, namespace `zivo` |
| Database | `zivo` |
| DB user (in-cluster) | `zivo` |

zivo's production database is a Postgres (+ pgvector) pod in the `zivo`
namespace on the VPS, not a managed RDS instance. Reach it read-only from your
machine by port-forwarding the pod with `kubectl` and connecting with `psql`.

If the SSH or port-forward connection fails, stop and ask the user to verify
access. Always take a DB backup before any risky migration — see
`agents/prod-safety.md`.

## Running Read-Only Queries

SSH to the VPS, set the kubeconfig, and query the in-cluster Postgres directly.
Keep all queries read-only (`SELECT`, `EXPLAIN`); never run `INSERT`, `UPDATE`,
`DELETE`, `DROP`, `TRUNCATE`, or DDL without a separate state-changing approval.

```bash
ssh zivo-vps 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml; \
  kubectl -n zivo exec statefulset/zivo-postgres -- \
  psql -U zivo -d zivo -c "<SQL>"'
```

Alternatively, port-forward to your machine and run `psql` locally:

```bash
# Terminal 1: forward the in-cluster Postgres to localhost:5453
ssh zivo-vps 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml; \
  kubectl -n zivo port-forward statefulset/zivo-postgres 5453:5432'

# Terminal 2: query over the forwarded port
psql -h 127.0.0.1 -p 5453 -U zivo -d zivo -c "<SQL>"
```

## Finding the Exact Pod/StatefulSet Name

If the StatefulSet name differs, list candidates first:

```bash
ssh zivo-vps 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml; kubectl -n zivo get statefulset'
ssh zivo-vps 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml; kubectl -n zivo get pods -l app.kubernetes.io/name=postgres'
```
