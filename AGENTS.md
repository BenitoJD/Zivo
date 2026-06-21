# Question Better. — agent / developer guide

Monorepo for [zivo.fyi](https://zivo.fyi). Engineering codename: **Zivo**. Product brand: **Question Better.**

Same deployment model as [citepage](https://github.com/BenitoJD/citepage): **K3s + Helm on one VPS**; Docker Compose only for local Postgres and MinIO.

**Product direction:** measure and improve understanding through questions. Year 1 = best AI-powered question generator. Strategy: [docs/VISION.md](docs/VISION.md).

## Readiness checklist

| Area | Status | Notes |
|------|--------|-------|
| **Repo structure** | Ready | Monolith: `backend/`, `frontend/`, `infra/k8s/`, `scripts/` |
| **DB + extensions** | Ready | Postgres 16, pgvector — foundation for question graph |
| **Legacy `intel` schema** | Present | `backend/schema/intel_foundation.sql` — prior scaffold; **replace with question-graph DDL next** |
| **API skeleton** | Ready | FastAPI + `/health`, `/health/ready` |
| **Frontend skeleton** | Ready | Next.js App Router, standalone Docker build |
| **Local dev** | Ready | `./scripts/dev.sh` + `docker-compose.yml` (deps only) |
| **Helm / K8s** | Ready | postgres, minio, api, web, db-schema charts |
| **CI** | Ready | `.github/workflows/ci.yml` |
| **Deploy workflow** | Ready | `.github/workflows/deploy.yml` (needs push + workflow run) |
| **VPS base** | Ready | K3s, Traefik, cert-manager, GH runner at `103.194.228.47` |
| **VPS app stack** | Empty | Run deploy after code is on `main` |
| **Question generation** | **Next** | Upload source → MCQs + explanations + difficulty |
| **Question evaluation** | Not started | Quality / ambiguity / discrimination scoring |
| **Answer intelligence** | Not started | Capture responses → improve calibration |
| **Question graph** | Not started | Concepts, prerequisites, item metadata |
| **Workers** | Stub | `backend/app/workers/` — generation + eval jobs go here |

**Start building the question engine.** Infrastructure scaffolding is in place.

## Layout

```
zivo/
├── docs/
│   └── VISION.md         # product strategy and 10-year roadmap
├── .cursor/skills/       # agent skills (zivo-dev, fastapi, postgres, …)
├── agents/               # prod-safety, testing notes
├── backend/
│   ├── app/
│   │   ├── api/          # HTTP routes — question endpoints here
│   │   └── workers/      # generation, evaluation, embedding jobs
│   ├── schema/           # SQL DDL (question graph next)
│   └── scripts/
├── frontend/             # Next.js App Router
├── infra/k8s/            # Helm charts + prod values
├── scripts/              # dev.sh, bootstrap-vps.sh, deploy helpers
├── docker-compose.yml    # local postgres + minio only
└── .github/workflows/
```

## Stack

| Layer | Choice |
|-------|--------|
| API | FastAPI, SQLAlchemy, psycopg |
| DB | PostgreSQL 16 + pgvector |
| Object storage | MinIO (S3-compatible) — source uploads (PDFs, transcripts) |
| UI | Next.js 15 (App Router) |
| Local deps | Docker Compose (`postgres`, `minio`) |
| Deploy | K3s, Helm, Traefik, cert-manager, GHCR, GitHub Actions |

## Local development

### Prerequisites

Python 3.12+, Node.js 22+, Docker (for Postgres + MinIO).

### Commands

```bash
./scripts/dev.sh setup
./scripts/dev.sh start              # API → http://127.0.0.1:8200
./scripts/dev.sh db schema          # apply current schema SQL
./scripts/dev.sh doctor
./scripts/dev.sh stop

cd frontend && npm install && npm run dev   # → http://localhost:3000
```

### Ports (local)

| Service | Port |
|---------|------|
| API | `8200` |
| Next.js | `3000` |
| Postgres | `5455` |
| MinIO API / console | `9020` / `9021` |

### Environment

- `backend/.env.example` — defaults
- `backend/.env.local` — your overrides (gitignored)
- `frontend/.env.local` — `NEXT_PUBLIC_API_URL` if needed

## Schema

DDL lives in `backend/schema/`. **No Alembic** — apply via:

```bash
./scripts/dev.sh db schema
```

Next milestone: `question_graph.sql` (concepts, questions, items, attempts, calibration). Until then, `intel_foundation.sql` remains applied for infra compatibility.

## Production (VPS)

| | |
|---|---|
| **Host** | `103.194.228.47` (`ssh zivo-vps`) |
| **DNS** | `zivo.fyi`, `www.zivo.fyi`, `api.zivo.fyi`, `s3.zivo.fyi` → VPS IP |
| **Namespace** | `zivo` |
| **Runner labels** | `self-hosted`, `zivo` |

First-time server setup (if secrets were wiped):

```bash
RUNNER_TOKEN=$(gh api --method POST repos/BenitoJD/Zivo/actions/runners/registration-token --jq .token)
ssh zivo-vps "RUNNER_TOKEN=$RUNNER_TOKEN bash -s" < scripts/bootstrap-vps.sh
```

Deploy: GitHub → Actions → **Deploy Zivo** → Run workflow.

## Conventions

- Business copy: root `README.md` only.
- Product strategy: `docs/VISION.md`.
- Schema changes: `backend/schema/` only.
- New routes: `backend/app/api/`.
- Background jobs: `backend/app/workers/`.
- UI: `frontend/app/`.

## Skills

Cursor skills live in `.cursor/skills/`. Start with **`zivo-dev`** for local workflow; use `fastapi`, `postgres`, `production-release-deploy`, `debug-kubernetes` as needed.
