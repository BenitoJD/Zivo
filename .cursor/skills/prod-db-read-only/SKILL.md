---
name: "prod-db-read-only"
description: "Read-only access to the citepage production PostgreSQL database on the VPS via kubectl port-forward and psql. Use when the user asks to query prod DB, check production data, investigate live issues, or analyze production records."
---

# Production Database Read-Only Access

## Connection Details

| Parameter | Value |
|-----------|-------|
| Prod host | VPS (`ssh citepage-vps`) running K3s |
| Postgres | in-cluster StatefulSet, Helm release `citepage-postgres`, namespace `citepage` |
| Database | `citepage` |
| DB user (in-cluster) | `citepage` |

citepage's production database is a Postgres (+ pgvector) pod in the `citepage`
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
ssh citepage-vps 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml; \
  kubectl -n citepage exec statefulset/citepage-postgres -- \
  psql -U citepage -d citepage -c "<SQL>"'
```

Alternatively, port-forward to your machine and run `psql` locally:

```bash
# Terminal 1: forward the in-cluster Postgres to localhost:5453
ssh citepage-vps 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml; \
  kubectl -n citepage port-forward statefulset/citepage-postgres 5453:5432'

# Terminal 2: query over the forwarded port
psql -h 127.0.0.1 -p 5453 -U citepage -d citepage -c "<SQL>"
```

## Finding the Exact Pod/StatefulSet Name

If the StatefulSet name differs, list candidates first:

```bash
ssh citepage-vps 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml; kubectl -n citepage get statefulset'
ssh citepage-vps 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml; kubectl -n citepage get pods -l app.kubernetes.io/name=postgres'
```
