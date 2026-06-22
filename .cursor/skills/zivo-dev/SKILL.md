---
name: zivo-dev
description: Zivo local development workflow — scripts/dev.sh, intel schema, docker deps, Next.js frontend. Use when setting up, starting, or debugging the Zivo dev stack.
---

# Zivo dev skill

Use for local development on the Zivo monorepo.

## Quick reference

```bash
./scripts/dev.sh setup          # ~/.venv/zivo + pip install
./scripts/dev.sh start          # deps + alembic migrate + API on :8200
./scripts/dev.sh stop
./scripts/dev.sh doctor
./scripts/dev.sh db migrate     # alembic upgrade head
./scripts/dev.sh db seed        # question vocab seeds

cd frontend && npm install && npm run dev   # Next.js on :3000
```

## Layout

| Path | Purpose |
|------|---------|
| `backend/app/api/` | FastAPI routes |
| `backend/app/workers/` | Ingest / normalize jobs (add here) |
| `backend/schema/*.sql` | DDL source for baseline Alembic revisions |
| `backend/alembic/versions/` | Alembic migration chain |
| `frontend/app/` | Next.js App Router UI — **Mantine only** (no `components/`) |
| `frontend/lib/` | API client, types, constants (not UI) |
| `infra/k8s/` | Helm charts + prod values |
| `logs/zivo-dev/` | API logs from `dev.sh start` |

## Environment

- Defaults: `backend/.env.example`
- Local overrides: `backend/.env.local` (create manually, gitignored)
- Frontend: `frontend/.env.local` (`NEXT_PUBLIC_API_URL` if needed)

## Schema

Zivo uses **Alembic**. Baseline revisions execute `backend/schema/*.sql`; new changes add revisions under `backend/alembic/versions/`.

```bash
./scripts/dev.sh db migrate
backend/scripts/test_alembic_migrations.sh   # upgrade / downgrade smoke test
```

Production: K8s Job `alembic-migrate` via `scripts/run-k8s-schema-migrate.sh` before API rollout.

If your local DB was created with the old `db schema` scripts and has no `alembic_version` row, stamp once: `cd backend && alembic stamp head`.

## Docker deps

`docker-compose.yml` — local only:

| Service | Host port |
|---------|-----------|
| postgres (pgvector) | `5455` |
| minio | `9020` / `9021` |

API and Next.js run **on the host**, not in compose.

## Frontend UI

- **Mantine only** — compose screens in `frontend/app/` from `@mantine/*` imports.
- **Never add** `frontend/components/`, custom CSS, or non-Mantine UI libraries.
- Icons: `@tabler/icons-react` only.
- Details: [AGENTS.md](../../AGENTS.md#frontend-ui).

## VPS

- SSH: `ssh zivo-vps` (`103.194.228.47`)
- Prod: K3s namespace `zivo`, deploy via GitHub Actions `Deploy Zivo`

## Related skills

- `debug-fastapi` — API debugging
- `fastapi` — route/service patterns
- `postgres` / `postgres-patterns` — intel schema work
- `production-release-deploy` — K3s deploy flow
- `debug-kubernetes` — cluster issues
