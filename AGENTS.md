# Question Better. — agent / developer guide

Monorepo for [zivo.fyi](https://zivo.fyi). Engineering codename: **Zivo**. Product brand: **Question Better.**

Same deployment model as [zivo](https://github.com/BenitoJD/zivo): **K3s + Helm on one VPS**; Docker Compose only for local Postgres and MinIO.

**Product direction:** measure and improve understanding through questions. Year 1 = best AI-powered question generator. Strategy: [docs/VISION.md](docs/VISION.md). **Data model (all tables):** [docs/DATA_MODEL.md](docs/DATA_MODEL.md).

**How we build (read before shipping):** coding rules → [docs/CONVENTIONS.md](docs/CONVENTIONS.md) · design decisions → [docs/adr/](docs/adr/0000-index.md) · UI → [Frontend UI](#frontend-ui) below. These three are the single source of truth; anything under `.cursor/skills/` that contradicts them is stale.

**Ship gates (reach gold):** before commit, push, or deploy, run `./scripts/ship-gates.sh` — see [agents/ship-gates.md](agents/ship-gates.md). Agents must not hand off work or trigger deploy until gates pass.

## Holy grail (engines)

**Code is the enemy.**
**Code should run, not the coder.**
**Do not cram in if-else into code. Don't simply code. Build engines.**

Decisions (rank, metric, gate, schedule, next-step) live behind named engine facades with typed verdicts and `policy_version`. Orchestration loads signals, calls the facade, persists or serves. It does not own the if-else. Plumbing (auth, storage, parse, jobs, HTTP) stays services. Index: [docs/ENGINES.md](docs/ENGINES.md). Seam: [ADR 0004](docs/adr/0004-swappable-policy-seam.md).

## Readiness checklist

| Area | Status | Notes |
|------|--------|-------|
| **Repo structure** | Ready | Monolith: `backend/`, `frontend/`, `infra/k8s/`, `scripts/` |
| **DB + extensions** | Ready | Postgres 16, pgvector — foundation for question graph |
| **Legacy `intel` schema** | Present | `intel_foundation.sql` unchanged; product data in `intel.*` |
| **QB app schema** | Ready | `qb_app.sql` + `qb_infra.sql` via Alembic (`./scripts/dev.sh db migrate`) |
| **API** | Ready | FastAPI — auth, sources, artifacts, activities, assertions, chat, mcq |
| **Workers** | Ready | Zivo ETA IO+CPU — `backend/app/eta/`, `run_eta_worker_*.py` |
| **Workspace UI** | Ready | `/workspace` — Mantine `AppShell`, Learn/Test layout in `app/` routes |
| **Helm / K8s** | Ready | postgres, minio, api, web, db-schema charts |
| **CI** | Ready | `.github/workflows/ci.yml` — self-hosted `zivo` runner on VPS |
| **Deploy workflow** | Ready | `.github/workflows/deploy.yml` (needs push + workflow run) |
| **VPS base** | Ready | K3s, Traefik, cert-manager, GH runner at `103.194.228.47` |
| **VPS app stack** | Empty | Run deploy after code is on `main` |
| **Question generation** | Ready | Upload source → MCQs; Budget + Quality + Graph + priors |
| **Question evaluation** | Ready (engine) | `docs/QUALITY_EVALUATION_ENGINE.md` + `quality_evaluation.py` |
| **Adaptive selection** | Ready (engine) | `docs/ADAPTIVE_SELECTION_ENGINE.md` + `adaptive_selection.py` |
| **Calibration** | Ready (engine) | `docs/CALIBRATION_ENGINE.md` + `calibration_engine.py` |
| **Engines index** | Ready | [docs/ENGINES.md](docs/ENGINES.md) - 18 Year-1+ engines (holy grail) |
| **Answer intelligence** | Ready (engine) | Measurements + Elo + mastery stop + spaced revisit on grade |
| **Question graph** | Ready (engine) | `docs/QUESTION_GRAPH_ENGINE.md` + lineage at cook |

## Layout

```
zivo/
├── docs/
│   ├── VISION.md         # product strategy and 10-year roadmap
│   ├── ENGINES.md        # index of all policy engines
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

./scripts/ship-gates.sh           # lint + tests + build (before commit/deploy)
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

Product tables: `intel.*` (unchanged DDL) + additive `qb.*` — see [docs/WORKSPACE.md](docs/WORKSPACE.md) and [ADR 0002](docs/adr/0002-intel-frozen-qb-additive.md).

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
| **Where UI lives** | `frontend/app/` — pages, layouts (`layout.tsx`, `page.tsx`, `providers.tsx`), and collocated private components in `app/**/_components/` (underscore folders are excluded from routing) |
| **Forbidden** | top-level `frontend/components/`, custom CSS files, Tailwind, shadcn. Inline `style={}` is permitted only for dynamic/animated values — prefer Mantine style props otherwise |
| **Allowed imports** | `@mantine/*`, `@tabler/icons-react` (Mantine's icon set), `next/*`, `react` |
| **Non-UI code** | `frontend/lib/` — API client, types, constants, and shared client logic (e.g. `lib/auth.ts`) |
| **Theming** | `createTheme` in `app/providers.tsx` — Mantine theme API, not custom stylesheets |

### Calm Paper Style System

Zivo's UI is a **light-first, book-like study aesthetic** ("Calm Paper"): warm paper surfaces, ink text, restrained color, generous air, tactile cards — calm, not loud. When editing or creating interface files, you **MUST** strictly adhere to these tokens (all defined in `app/providers.tsx`):

#### 1. Typography
- **Serif / reading content** (`var(--font-serif)`, Newsreader): question stems, page/section titles, empty-state headings, empty/complete states, marketing headlines. Weight `500`; `italic` sparingly for highlights.
- **Sans / UI** (`var(--font-sans)`, Plus Jakarta Sans): body copy, inputs, buttons, navigation, metadata. Compact and clean.
- **Line-height**: generous for reading (theme `lineHeights` scale; body `1.6`+).

#### 2. Color Palette
Colors resolve via `cssVariablesResolver` + the theme color scales. **Default scheme is light.**
- **Brand accent — `lavender`**: used *sparingly* — links, active states, selection rings, primary action buttons. Prefer `lavender.6`/`lavender.7` (text) and `lavender.0`/`lavender-1` (tint backgrounds).
- **Neutral — `dark`/`gray` (aliased, warm paper→ink scale)**: surfaces and text.
- **Feedback (calm, not loud)**: `sage` = correct/success; `terracotta` = wrong/error. `green`/`red`/`blue` are **aliased** to these so legacy `color="green"/"red"/"blue"` resolves to the calm palette — but write new code as `sage`/`terracotta`/`lavender` explicitly.
- **Light Scheme**: body `#F4F1E9` (warm oat paper) · text `#232220` (ink) · border `#E7E2D6` (warm hairline) · hover `#EFEBE1` · card surface `gray.0` (`#FBFAF6`).
- **Dark Scheme**: body `#1A1917` (warm ink) · text `#F4F1E9` (paper) · border `#2E2C28` · hover `#262522`.
- **No hardcoded hex in pages/components.** Use Mantine tokens (`var(--mantine-color-sage-0)`, `c="lavender.7"`, `bg="gray.0"`, etc.).

#### 3. Shadows
Soft "paper lift" — calm, not Material-heavy. Use `shadow="paper"` (default on `Paper`) or `shadow="paper-lg"` for elevated hero cards/modals.

#### 4. Border Radii
Soft and tactile: `Button`/`SegmentedControl` = `xl` (pill); `Paper`/`Modal` = `xl` (`28px`); `TextInput`/`PasswordInput`/`Textarea` = `md` (crisp). Radius scale in theme: `xs 6 / sm 10 / md 14 / lg 20 / xl 28`.

#### 5. Frosted Modals
All modals use a frosted backdrop. `overlayProps` = `{ backgroundOpacity: 0.45, blur: 8 }` (set as the `Modal` default).

#### 6. Motion
Transitions use `cubic-bezier(0.32, 0.72, 0, 1)` over ~280ms; always respect `prefers-reduced-motion`.

### Routes

- `/` — public landing page (`app/page.tsx`)
- `/login`, `/signup` — dedicated auth pages (`app/login/page.tsx`, `app/signup/page.tsx`); shared form in `app/_components/AuthForm.tsx`, logic in `lib/auth.ts`
- `/learn` — public Learn posts (no learner sidebar)
- `/workspace/**`, `/practice/**` — learner AppShell + Sidebar via route group `app/(shell)/layout.tsx` (URLs unchanged)
- `/workspace` — library / empty-state (`app/(shell)/workspace/page.tsx`)
- `/workspace/[artifactId]` — page selection → MCQ → source + tutor
- `/workspace/models` — admin model management
- `/practice` — practice hub; `/practice/coding`, `/practice/newspaper`, `/practice/system-design`, `/practice/c/[qid]`
- Shared shell (sidebar, add-source modal, delete modal) — `app/(shell)/layout.tsx` wraps `/workspace/**` and `/practice/**` (`Sidebar`, `AddSourceModal`, `DeleteSourceModal` under `app/(shell)/workspace/_components/`). Auth is handled by `/login`, not a modal.

```bash
cd frontend && npm run build && npm run lint
```

## Conventions

**Coding practices are documented once in [docs/CONVENTIONS.md](docs/CONVENTIONS.md)**
(layering, raw-SQL repositories, the ETA worker pattern, the swappable-policy seam,
error handling, typing, naming, Alembic, testing) — each rule with a `file:line` proof.
Design decisions are in [docs/adr/](docs/adr/0000-index.md). Quick pointers:

- Business copy: root `README.md` only.
- Product strategy: `docs/VISION.md`. · Engines: `docs/ENGINES.md`. · Table reference: `docs/DATA_MODEL.md`.
- Schema changes: `backend/alembic/versions/` (+ update `backend/schema/*.sql` when baselining raw SQL).
- New routes: `backend/app/api/`. · Background jobs: `backend/app/eta/`.
- UI: **`frontend/app/` only** — see [Frontend UI](#frontend-ui) above.
- Decisions: engines, not if-else in call sites — see [Holy grail](#holy-grail-engines).
- Agent writing: no em dashes (`—`); see `.cursor/rules/no-em-dashes.mdc`.

## Skills

Cursor skills live in `.cursor/skills/`. Start with **`zivo-dev`** for local workflow;
use `fastapi`, `postgres`, `production-release-deploy`, `debug-kubernetes` as needed.

**Skills do not fork the rules.** [docs/CONVENTIONS.md](docs/CONVENTIONS.md) and
[docs/adr/](docs/adr/0000-index.md) are canonical; a skill links to them rather than
restating a rule. The skills were ported from another project and have been reconciled
to this repo (a `frontend/` + `npm` codebase); if you ever find one that contradicts the
canonical docs above, treat the docs as correct and fix the skill in the same PR.
