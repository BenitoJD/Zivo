---
name: zivo-dev
description: Zivo local development workflow — scripts/dev.sh, intel schema, docker deps, Next.js frontend. Use when setting up, starting, or debugging the Zivo dev stack.
---

# Zivo dev skill

Use for local development on the Zivo monorepo.

## Quick reference

```bash
./scripts/dev.sh setup          # ~/.venv/zivo + pip install
./scripts/dev.sh start          # deps + schema + API on :8200
./scripts/dev.sh stop
./scripts/dev.sh doctor
./scripts/dev.sh db schema      # apply intel_foundation.sql

cd frontend && npm install && npm run dev   # Next.js on :3000
```

## Layout

| Path | Purpose |
|------|---------|
| `backend/app/api/` | FastAPI routes |
| `backend/app/workers/` | Ingest / normalize jobs (add here) |
| `backend/schema/intel_foundation.sql` | Postgres intel DDL (source of truth) |
| `frontend/app/` | Next.js App Router UI |
| `infra/k8s/` | Helm charts + prod values |
| `logs/zivo-dev/` | API logs from `dev.sh start` |

## Environment

- Defaults: `backend/.env.example`
- Local overrides: `backend/.env.local` (create manually, gitignored)
- Frontend: `frontend/.env.local` (`NEXT_PUBLIC_API_URL` if needed)

## Schema

Zivo uses **SQL schema files**, not Alembic. Apply locally:

```bash
./scripts/dev.sh db schema
```

Production: K8s Job via `scripts/run-k8s-schema-migrate.sh` before API rollout.

## Docker deps

`docker-compose.yml` — local only:

| Service | Host port |
|---------|-----------|
| postgres (pgvector) | `5455` |
| minio | `9020` / `9021` |

API and Next.js run **on the host**, not in compose.

## VPS

- SSH: `ssh zivo-vps` (`103.194.228.47`)
- Prod: K3s namespace `zivo`, deploy via GitHub Actions `Deploy Zivo`

## Related skills

- `debug-fastapi` — API debugging
- `fastapi` — route/service patterns
- `postgres` / `postgres-patterns` — intel schema work
- `production-release-deploy` — K3s deploy flow
- `debug-kubernetes` — cluster issues
