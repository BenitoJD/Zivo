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

Decisions (rank, metric, gate, schedule, next-step, plumbing) live behind named engine facades whose bodies are **rule tables**, not if-else. Typed verdicts carry `policy_version`. Callers load signals, call the facade, and `apply()` the verdict via [engine_runtime](backend/app/engine_runtime.py). First-party Python/TS/JS has no `if` / `elif` / `else` / ternary token ([ADR 0018](docs/adr/0018-zero-if-engine-tables.md)). Index: [docs/ENGINES.md](docs/ENGINES.md). Seam: [ADR 0004](docs/adr/0004-swappable-policy-seam.md).

## Readiness checklist

| Area | Status | Notes |
|------|--------|-------|
| **Repo structure** | Ready | `backend/`, `auth/`, `storage/`, `practice/`, `content/`, `study/`, `library/`, `admin/`, `frontend/`, `infra/k8s/`, `scripts/` |
| **DB + extensions** | Ready | Postgres 16, pgvector — foundation for question graph |
| **Legacy `intel` schema** | Present | `intel_foundation.sql` unchanged; product data in `intel.*` |
| **QB app schema** | Ready | `qb_app.sql` + `qb_infra.sql` via Alembic (`./scripts/dev.sh db migrate`) |
| **API** | Retired | No health-only `zivo-api`. Product HTTP is owned services; product Alembic is `zivo-migrate`. [ADR 0017](docs/adr/0017-owned-copy-no-api-shell.md) |
| **Auth service** | Ready | Identity FastAPI (`auth/`): signup, login, logout, session, Google OAuth. [ADR 0007](docs/adr/0007-auth-microservice.md) |
| **Storage service** | Ready | Object FastAPI (`storage/`): MinIO writes, chunked upload, `storage.*`. [ADR 0008](docs/adr/0008-storage-microservice.md) |
| **Practice service** | Ready | Coding / system-design / newspaper practice HTTP (`practice/`). [ADR 0010](docs/adr/0010-practice-microservice.md) |
| **Content service** | Ready | SEO `/learn` + newspaper admin HTTP (`content/`). [ADR 0011](docs/adr/0011-content-microservice.md) |
| **Study service** | Ready | Learn queue, MCQ, chat, guest, offline (`study/`). [ADR 0012](docs/adr/0012-study-microservice.md) |
| **Library service** | Ready | Sources, documents, activities, audiobook (`library/`). [ADR 0013](docs/adr/0013-library-microservice.md) |
| **Admin service** | Ready | Models + debug HTTP (`admin/`). [ADR 0014](docs/adr/0014-admin-microservice.md) |
| **Workers (jobs)** | Ready | Slim `zivo-worker` image; ETA IO+CPU lease `qb.jobs`. [ADR 0009](docs/adr/0009-jobs-workers-are-the-process.md) |
| **Workspace UI** | Ready | `/workspace` — Mantine `AppShell`, Learn/Test layout in `app/` routes |
| **Helm / K8s** | Ready | postgres, minio, auth, storage, practice, content, study, library, admin, web, worker, db-schema charts |
| **CI/CD pipeline** | Ready | `.github/workflows/cicd.yml` — unit tests, GHCR images (semver on `main`, sha on branches), `production` approval gate, ArgoCD promote; setup: [.github/workflows/README.md](.github/workflows/README.md) |
| **HA k8s cluster** | Ready | 3 masters + 5 workers + 2 LBs on Web Eye Soft VPS — see [infra/SERVERS.md](infra/SERVERS.md) |
| **App stack** | Ready | Full Zivo stack deployed on the HA cluster; DNS cutover to `45.196.196.98` is the open step before TLS. |
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
│   │   ├── api/          # shared health helper; product routers live in *_api packages
│   │   └── workers/      # generation, evaluation, embedding jobs
│   ├── schema/           # SQL DDL (question graph next)
│   └── scripts/
├── auth/                 # identity FastAPI: signup, login, session, Google OAuth
├── storage/              # object FastAPI: MinIO writes, chunked upload, storage.*
├── practice/             # coding / system-design / newspaper practice HTTP
├── content/              # SEO /learn + newspaper admin HTTP
├── study/                # learn queue, MCQ, chat, guest, offline
├── library/              # sources, documents, activities, audiobook HTTP
├── admin/                # models + debug HTTP
├── workers/              # slim ETA worker image
├── frontend/             # Next.js App Router UI — Mantine-only UI in app/
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
./scripts/dev.sh start              # auth + storage + practice + content + study + library + admin + workers + Next.js (:8201-:8207, :3000)
./scripts/dev.sh db migrate        # auth, storage, practice, content, study, library, admin Alembic, then product Alembic
./scripts/dev.sh db seed           # question vocab seeds
./scripts/dev.sh doctor
./scripts/dev.sh stop

./scripts/ship-gates.sh           # lint + tests + build (before commit/deploy)
cd frontend && npm run build && npm run lint
```

### Ports (local)

| Service | Port |
|---------|------|
| Auth | `8201` |
| Storage | `8202` |
| Practice | `8203` |
| Content | `8204` |
| Study | `8205` |
| Library | `8206` |
| Admin | `8207` |
| Next.js | `3000` |
| Postgres | `5455` |
| MinIO API / console | `9020` / `9021` |

### Environment

- `backend/.env.example` : defaults (shared `SECRET_KEY` / `DATABASE_URL` with auth)
- `backend/.env.local` : your overrides (gitignored)
- `auth/.env.example` : identity-service defaults (same Postgres)
- `frontend/.env.local` : `NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_AUTH_URL` / `NEXT_PUBLIC_STORAGE_URL` if needed (empty locally so Next rewrites `/api/auth` to `:8201`, `/api/storage` to `:8202`, and the other HTTP slices to `:8203`-`:8207`)

## Schema

DDL source files live in `backend/schema/`, `auth/schema/`, `storage/schema/`, `practice/schema/`, `content/schema/`, `study/schema/`, `library/schema/`, and `admin/schema/`. **Alembic** applies them (`./scripts/dev.sh db migrate` runs auth, storage, practice, content, study, library, admin, then product):

```bash
./scripts/dev.sh db migrate
./scripts/dev.sh db seed
```

Product migrations: `backend/alembic/versions/` (`001_intel_foundation` → `002_qb_schema`, …). New product schema changes: add a revision with `cd backend && alembic revision --autogenerate -m "message"`, then run `backend/scripts/test_alembic_migrations.sh`.

Identity migrations: `auth/alembic/versions/` with version table `alembic_version_auth` (only `auth.*`). Storage migrations: `storage/alembic/versions/` with `alembic_version_storage` (only `storage.*`). Practice / content / study / library / admin each have `alembic_version_*` and an exclusive schema marker; shared `qb.*` / `intel.*` stay on product Alembic. Product FKs stay on `qb.account`; credentials live in `auth.account`. See [ADR 0007](docs/adr/0007-auth-microservice.md), [ADR 0008](docs/adr/0008-storage-microservice.md), [ADR 0010](docs/adr/0010-practice-microservice.md), [ADR 0011](docs/adr/0011-content-microservice.md), [ADR 0012](docs/adr/0012-study-microservice.md), [ADR 0013](docs/adr/0013-library-microservice.md), [ADR 0014](docs/adr/0014-admin-microservice.md), [ADR 0015](docs/adr/0015-slim-process-images.md), [ADR 0016](docs/adr/0016-owned-http-packages.md), [ADR 0017](docs/adr/0017-owned-copy-no-api-shell.md).

Product tables: `intel.*` (unchanged DDL) + additive `qb.*` — see [docs/WORKSPACE.md](docs/WORKSPACE.md) and [ADR 0002](docs/adr/0002-intel-frozen-qb-additive.md).

## Production (HA Kubernetes cluster)

| | |
|---|---|
| **App entry (LB)** | `45.196.196.98` (HAProxy → Traefik on workers) |
| **K8s API entry (LB)** | `45.196.196.233:8443` (`KUBECONFIG=~/.kube/zivo-ha.conf`) |
| **DNS** | `zivo.fyi`, `www.zivo.fyi`, `api.zivo.fyi`, `auth.zivo.fyi`, `storage.zivo.fyi`, `practice.zivo.fyi`, `content.zivo.fyi`, `study.zivo.fyi`, `library.zivo.fyi`, `admin.zivo.fyi`, `s3.zivo.fyi` → `45.196.196.98` |
| **Namespace** | `zivo` |
| **Runner** | Self-hosted on worker node4, labels `self-hosted`, `zivo` |

**Fleet servers (Web Eye Soft):** the 10-VPS inventory (roles, specs, SSH aliases `zivo-lb1`, `zivo-lb2`, `zivo-node1`–`zivo-node8`, renewals) lives in [infra/SERVERS.md](infra/SERVERS.md).

**Full rebuild (bare Ubuntu → production):** inject the fleet key, then `ansible-playbook infra/ansible/site.yml`, then `KUBECONFIG=~/.kube/zivo-ha.conf ./scripts/install-ha-platform.sh`; ArgoCD then syncs every release from git on the first `main` push through the `cicd` pipeline. Runbook: [infra/SERVERS.md](infra/SERVERS.md).

Deploy: push to `main` → `cicd` tests, builds, and pushes images to GHCR → approve the `production` environment gate → the promote commit makes ArgoCD roll out. (Manual re-run: Actions → **cicd** → Run workflow.)

Google OAuth redirect URI in production is `https://auth.zivo.fyi/api/auth/google/callback` (Google Cloud Console + `GOOGLE_REDIRECT_URI` in `zivo-secrets`). Every hostname's A record must point at `45.196.196.98` before TLS will issue. Until then, keep those Ingresses HTTP-only and leave `NEXT_PUBLIC_*` empty so apex rewrites hit in-cluster services.

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
- `/quiz` — public Quiz Share: build an MCQ set (AI-drafted or manual), share `/quiz/[slug]`, results at `/quiz/[slug]/manage` (`app/quiz/`)
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
