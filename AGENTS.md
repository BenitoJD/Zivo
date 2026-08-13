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
| **Repo structure** | Ready | `backend/`, `auth/`, `frontend/`, `infra/k8s/`, `scripts/` |
| **DB + extensions** | Ready | Postgres 16, pgvector — foundation for question graph |
| **Legacy `intel` schema** | Present | `intel_foundation.sql` unchanged; product data in `intel.*` |
| **QB app schema** | Ready | `qb_app.sql` + `qb_infra.sql` via Alembic (`./scripts/dev.sh db migrate`) |
| **API** | Ready | FastAPI product API: sources, artifacts, activities, assertions, chat, mcq |
| **Auth service** | Ready | Identity FastAPI (`auth/`): signup, login, logout, session, Google OAuth. [ADR 0007](docs/adr/0007-auth-microservice.md) |
| **Workers** | Ready | Zivo ETA IO+CPU — `backend/app/eta/`, `run_eta_worker_*.py` |
| **Workspace UI** | Ready | `/workspace` — Mantine `AppShell`, Learn/Test layout in `app/` routes |
| **Helm / K8s** | Ready | postgres, minio, api, auth, web, db-schema charts |
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
| **Offline Mode** | Ready | Signed, server-tracked study packs — pre-load, study offline, sync grades. [ADR 0006](docs/adr/0006-offline-answer-keys.md) + `offline_pack.py` |

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
├── auth/                 # identity FastAPI: signup, login, session, Google OAuth
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
./scripts/dev.sh start              # API + auth + workers + Next.js (:8200, :8201, :3000)
./scripts/dev.sh db migrate        # auth Alembic then product Alembic
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
| Auth | `8201` |
| Next.js | `3000` |
| Postgres | `5455` |
| MinIO API / console | `9020` / `9021` |

### Environment

- `backend/.env.example` : defaults (shared `SECRET_KEY` / `DATABASE_URL` with auth)
- `backend/.env.local` : your overrides (gitignored)
- `auth/.env.example` : identity-service defaults (same Postgres)
- `frontend/.env.local` : `NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_AUTH_URL` if needed (empty locally so Next rewrites `/api/auth` to `:8201`)

## Schema

DDL source files live in `backend/schema/` and `auth/schema/`. **Alembic** applies them (`./scripts/dev.sh db migrate` runs auth first, then product):

```bash
./scripts/dev.sh db migrate
./scripts/dev.sh db seed
```

Product migrations: `backend/alembic/versions/` (`001_intel_foundation` → `002_qb_schema`, …). New product schema changes: add a revision with `cd backend && alembic revision --autogenerate -m "message"`, then run `backend/scripts/test_alembic_migrations.sh`.

Identity migrations: `auth/alembic/versions/` with version table `alembic_version_auth` (only `auth.*`). Product FKs stay on `qb.account`; credentials live in `auth.account`. See [ADR 0007](docs/adr/0007-auth-microservice.md).

Product tables: `intel.*` (unchanged DDL) + additive `qb.*` — see [docs/WORKSPACE.md](docs/WORKSPACE.md) and [ADR 0002](docs/adr/0002-intel-frozen-qb-additive.md).

## Production (VPS)

| | |
|---|---|
| **Host** | `103.194.228.47` (`ssh zivo-vps`) |
| **DNS** | `zivo.fyi`, `www.zivo.fyi`, `api.zivo.fyi`, `auth.zivo.fyi`, `s3.zivo.fyi` → VPS IP |
| **Namespace** | `zivo` |
| **Runner labels** | `self-hosted`, `zivo` |

First-time server setup (if secrets were wiped):

```bash
RUNNER_TOKEN=$(gh api --method POST repos/BenitoJD/Zivo/actions/runners/registration-token --jq .token)
ssh zivo-vps "RUNNER_TOKEN=$RUNNER_TOKEN bash -s" < scripts/bootstrap-vps.sh
```

Deploy: GitHub → Actions → **Deploy Zivo** → Run workflow.

Google OAuth redirect URI in production is `https://auth.zivo.fyi/api/auth/google/callback` (Google Cloud Console + `GOOGLE_REDIRECT_URI` in `zivo-secrets`). Add an `auth.zivo.fyi` A record to the VPS before TLS will issue.

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

## Agent tooling

**Graphify — query the codebase graph before grepping.** `graphify-out/graph.json`
maps the whole repo (6225 nodes, local tree-sitter AST, zero LLM cost) with an
agent skill at `.agents/skills/graphify/SKILL.md`. For architecture / relationship
questions, run `graphify query "..." --graph graphify-out/graph.json`,
`graphify path A B`, or `graphify explain X` instead of reading files one by one.
Regenerate with `graphify extract . --code-only --no-viz` (incremental; keep the
skill and graph.json committed, cache is gitignored).

**Work Checkpoint Engine — every long-running LLM job must resume, not redo.**
`app/services/work_checkpoint.py` (`qb.workckpt.v1`, [docs/WORK_CHECKPOINT_ENGINE.md](docs/WORK_CHECKPOINT_ENGINE.md)):
plan/mark_done/resume_work persist per-item progress on the ETA job row so a
pod death never re-bills completed LLM work. Any new multi-item job (audiobook,
notes, flashcards, mains, quiz, interview, generation) adopts this seam.

**Chrome MCP — UI work.** `cmd mcp` config has a `chrome` server
(`agent-browser mcp`). Use browser snapshots/click/type for UI testing; for
pure UI reads prefer the agent-browser CLI (`agent-browser open/snapshot/click`).

**Audiobook Engine — sources become listenable audio.** Local Piper TTS (MIT,
CPU-only, no API cost) + ffmpeg + LLM narration adaptation + per-chunk
checkpoint resume. [docs/AUDIOBOOK_ENGINE.md](docs/AUDIOBOOK_ENGINE.md). Kill
switch: `AUDIOBOOK_ENABLED` (default off; enabled in prod).

## Skills

Cursor skills live in `.cursor/skills/`. Start with **`zivo-dev`** for local workflow;
use `fastapi`, `postgres`, `production-release-deploy`, `debug-kubernetes` as needed.

**Skills do not fork the rules.** [docs/CONVENTIONS.md](docs/CONVENTIONS.md) and
[docs/adr/](docs/adr/0000-index.md) are canonical; a skill links to them rather than
restating a rule. The skills were ported from another project and have been reconciled
to this repo (a `frontend/` + `npm` codebase); if you ever find one that contradicts the
canonical docs above, treat the docs as correct and fix the skill in the same PR.
