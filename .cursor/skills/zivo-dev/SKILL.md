---
name: zivo-dev
description: Zivo local development workflow — scripts/dev.sh, intel schema, docker deps, Next.js frontend. Use when setting up, starting, or debugging the Zivo dev stack.
---

# Zivo dev skill

Use for local development on the Zivo monorepo.

## Quick reference

```bash
./scripts/dev.sh setup          # ~/.venv/zivo + pip install
./scripts/dev.sh start          # deps + migrate + API :8200 + auth :8201 + storage :8202 + practice :8203 + content :8204 + study :8205 + Next.js :3000
./scripts/dev.sh stop
./scripts/dev.sh doctor
./scripts/dev.sh db migrate     # alembic upgrade head
./scripts/dev.sh db seed        # question vocab seeds
```

## Layout

| Path | Purpose |
|------|---------|
| `backend/app/api/` | FastAPI routes |
| `backend/app/workers/` | Ingest / normalize jobs (add here) |
| `backend/schema/*.sql` | DDL source for baseline Alembic revisions |
| `backend/alembic/versions/` | Alembic migration chain |
| `auth/` | Identity FastAPI (signup, login, session, Google OAuth) |
| `storage/` | Object FastAPI (MinIO writes, chunked upload) |
| `practice/` | Coding / system-design / newspaper practice HTTP |
| `content/` | SEO /learn + newspaper admin HTTP |
| `study/` | Learn queue, MCQ, chat, artifacts workspace |
| `frontend/app/` | Next.js App Router UI — **Mantine only** (no `components/`) |
| `frontend/lib/` | API client, types, constants (not UI) |
| `infra/k8s/` | Helm charts + prod values |
| `logs/zivo-dev/` | API, auth, worker, and frontend logs from `dev.sh start` |

## Environment

- Defaults: `backend/.env.example` (shared `SECRET_KEY` / `DATABASE_URL` with auth)
- Local overrides: `backend/.env.local` (create manually, gitignored)
- Auth: `auth/.env.example` (same Postgres; prod host `auth.zivo.fyi`)
- Storage: `storage/.env.example` (same Postgres + MinIO; prod host `storage.zivo.fyi`)
- Frontend: `frontend/.env.local` (`NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_AUTH_URL` / `NEXT_PUBLIC_STORAGE_URL` if needed; empty locally)

## Schema

Zivo uses **Alembic**. `./scripts/dev.sh db migrate` applies `auth/` first
(`alembic_version_auth`), then `storage/` (`alembic_version_storage`), then
practice/content/study schema markers, then `backend/`. Baseline product
revisions execute `backend/schema/*.sql`; identity DDL is `auth/schema/auth.sql`;
object DDL is `storage/schema/storage.sql`.

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

API, workers, and Next.js run **on the host** via `dev.sh start` (not in compose).

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
- Ship gates before commit/deploy: [agents/ship-gates.md](../../agents/ship-gates.md) · `./scripts/ship-gates.sh`
- `production-release-deploy` — K3s deploy flow
- `debug-kubernetes` — cluster issues
