# Question Better. — agent / developer guide

Monorepo for [zivo.fyi](https://zivo.fyi). Engineering codename: **Zivo**. Product brand: **Question Better.**

Same deployment model as [citepage](https://github.com/BenitoJD/citepage): **K3s + Helm on one VPS**; Docker Compose only for local Postgres and MinIO.

**Product direction:** measure and improve understanding through questions. Year 1 = best AI-powered question generator. Strategy: [docs/VISION.md](docs/VISION.md). **Data model (all tables):** [docs/DATA_MODEL.md](docs/DATA_MODEL.md).

## Readiness checklist

| Area | Status | Notes |
|------|--------|-------|
| **Repo structure** | Ready | Monolith: `backend/`, `frontend/`, `infra/k8s/`, `scripts/` |
| **DB + extensions** | Ready | Postgres 16, pgvector — foundation for question graph |
| **Legacy `intel` schema** | Present | `intel_foundation.sql` unchanged; product data in `intel.*` |
| **QB app schema** | Ready | `qb_app.sql` + `qb_infra.sql` via Alembic (`./scripts/dev.sh db migrate`) |
| **API** | Ready | FastAPI — auth, sources, artifacts, activities, assertions, chat, mcq |
| **Workers** | Ready | Citepage ETA IO+CPU — `backend/app/eta/`, `run_eta_worker_*.py` |
| **Workspace UI** | Ready | `/workspace` — Mantine `AppShell`, Learn/Test layout in `app/` routes |
| **Helm / K8s** | Ready | postgres, minio, api, web, db-schema charts |
| **CI** | Ready | `.github/workflows/ci.yml` — self-hosted `zivo` runner on VPS |
| **Deploy workflow** | Ready | `.github/workflows/deploy.yml` (needs push + workflow run) |
| **VPS base** | Ready | K3s, Traefik, cert-manager, GH runner at `103.194.228.47` |
| **VPS app stack** | Empty | Run deploy after code is on `main` |
| **Question generation** | **Next** | Upload source → MCQs + explanations + difficulty |
| **Question evaluation** | Not started | Quality / ambiguity / discrimination scoring |
| **Answer intelligence** | Not started | Capture responses → improve calibration |
| **Question graph** | In progress | Concepts in `intel.*`; mastery via `qb.artifact_workspace` |

## Layout

```
zivo/
├── docs/
│   ├── VISION.md         # product strategy and 10-year roadmap
│   └── DATA_MODEL.md     # every intel table → Question Better use case
├── .cursor/skills/       # agent skills (zivo-dev, fastapi, postgres, …)
├── agents/               # prod-safety, testing notes
├── backend/
│   ├── app/
│   │   ├── api/          # HTTP routes — question endpoints here
│   │   └── workers/      # generation, evaluation, embedding jobs
│   ├── schema/           # SQL DDL (question graph next)
│   └── scripts/
├── frontend/             # Next.js App Router — Mantine-only UI in app/
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
| UI | Next.js 16 (App Router) + **Mantine 9** (`@mantine/core`, `hooks`, `form`, `dropzone`, `notifications`) |
| Local deps | Docker Compose (`postgres`, `minio`) |
| Deploy | K3s, Helm, Traefik, cert-manager, GHCR, GitHub Actions |

## Local development

### Prerequisites

Python 3.12+, Node.js 22+, Docker (for Postgres + MinIO).

### Commands

```bash
./scripts/dev.sh setup
./scripts/dev.sh start              # API + workers + Next.js (:8200, :3000)
./scripts/dev.sh db migrate        # alembic upgrade head
./scripts/dev.sh db seed           # question vocab seeds
./scripts/dev.sh doctor
./scripts/dev.sh stop

cd frontend && npm run build && npm run lint
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

DDL source files live in `backend/schema/`. **Alembic** applies them:

```bash
./scripts/dev.sh db migrate
./scripts/dev.sh db seed
```

Migrations: `backend/alembic/versions/` (`001_intel_foundation` → `002_qb_schema`). New schema changes: add a revision with `cd backend && alembic revision --autogenerate -m "message"`, then run `backend/scripts/test_alembic_migrations.sh`.

Product tables: `intel.*` (unchanged DDL) + additive `qb.*`. See [docs/WORKSPACE.md](docs/WORKSPACE.md) and [docs/CITEPAGE_PORT.md](docs/CITEPAGE_PORT.md).

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

## Frontend UI

**Mantine only. No custom components.**

| Rule | Detail |
|------|--------|
| **Where UI lives** | `frontend/app/` — pages and layouts (`layout.tsx`, `page.tsx`, `providers.tsx`) |
| **Forbidden** | `frontend/components/`, custom CSS files, inline `style={}`, native HTML UI (`<form>`, `<button>`, etc.), Tailwind, shadcn |
| **Allowed imports** | `@mantine/*`, `@tabler/icons-react` (Mantine’s icon set), `next/*`, `react` |
| **Non-UI code** | `frontend/lib/` — API client, types, constants only |
| **Theming** | `createTheme` in `app/providers.tsx` — Mantine theme API, not custom stylesheets |

### Wispr Flow Style System

Zivo utilizes an **editorial, human-centric, tactile UI/UX system** inspired by `wisprflow.ai`. When editing or creating new interface files, you **MUST** strictly adhere to the following layout, spacing, and styling tokens:

#### 1. Typography
- **Headings**: Use `var(--font-serif)` (Newsreader) with a lighter font weight (`500` or `400`). Use serif typography for high-impact titles, section labels, and empty-state headings. Incorporate italics (`fs="italic"`) sparingly for highlights.
- **Body & UI**: Use `var(--font-sans)` (Plus Jakarta Sans) for standard body copy, interactive controls, inputs, buttons, and navigation elements. Keep text size compact, high density, and clean.

#### 2. Color Palette
The colors are automatically resolved via `cssVariablesResolver` based on the active color scheme:
- **Primary Color**: `lavender` (brand purple/lilac). Use `var(--mantine-color-lavender-filled)` or `var(--mantine-color-lavender-light)` for primary buttons, active states, and highlights.
- **Light Scheme**:
  - Background (`--mantine-color-body`): `#FAF7EE` (Warm editorial cream)
  - Text (`--mantine-color-text`): `#1C1C1A` (Near black/charcoal text)
  - Borders (`--mantine-color-default-border`): `#E5E1D3` (Soft warm beige)
  - Hover states (`--mantine-color-default-hover`): `#F0EDE2`
- **Dark Scheme**:
  - Background (`--mantine-color-body`): `#121211` (Soft warm off-black)
  - Text (`--mantine-color-text`): `#FAF7EE` (Cream text)
  - Borders (`--mantine-color-default-border`): `#2E2D2B` (Soft charcoal border)
  - Hover states (`--mantine-color-default-hover`): `#1C1C1A`
- **Theme Overrides**: Avoid hardcoded hex colors. Rely on Mantine's theme variables or color scale tokens (e.g. `dark.0` through `dark.9` which maps to the warm cream/charcoal scale).

#### 3. Border Radii
Components must feel soft and tactile:
- **Pills**: Use `radius="xl"` (or `9999px`) for `Button`, `TextInput`, `PasswordInput`, and `SegmentedControl`.
- **Cards & Modals**: Use `radius="xl"` for containers, cards, and modal panels (`Paper` defaults to `xl` / `28px`).
- **Lists & Mini components**: Use `radius="md"` (`14px`) or `radius="lg"` (`20px`).

#### 4. Frosted Modals
All modals must have frosted glass backdrop filters. Ensure `overlayProps` uses:
- `backgroundOpacity: 0.6`
- `blur: 8`

Workspace routes:

- `/workspace` — empty library (`app/workspace/page.tsx`)
- `/workspace/[artifactId]` — setup, MCQ, source stub, tutor chat (`app/workspace/[artifactId]/page.tsx`)
- Shared shell (sidebar, add-source modal, auth modal) — `app/workspace/layout.tsx`

```bash
cd frontend && npm run build && npm run lint
```

## Conventions

- Business copy: root `README.md` only.
- Product strategy: `docs/VISION.md`.
- Table reference: `docs/DATA_MODEL.md`.
- Schema changes: `backend/alembic/versions/` (+ update `backend/schema/*.sql` when baselining raw SQL).
- New routes: `backend/app/api/`.
- Background jobs: `backend/app/eta/`.
- UI: **`frontend/app/` only** — see [Frontend UI](#frontend-ui) above.

## Skills

Cursor skills live in `.cursor/skills/`. Start with **`zivo-dev`** for local workflow; use `fastapi`, `postgres`, `production-release-deploy`, `debug-kubernetes` as needed.
