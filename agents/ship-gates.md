# Ship gates (reach gold before main)

**Gold** = the same checks CI runs on `main`, plus frontend lint. A change is not
ready to commit, merge, or deploy until ship gates pass locally.

CI runs only the unit-test suites (`.github/workflows/cicd.yml`); lint, import
smokes, and the frontend build are local ship gates. A red push wastes a full
runner cycle (tests + 11 Docker builds), so run gates **before** you commit or
push.

## When to run

| Moment | Required? |
|--------|-----------|
| Before committing backend or frontend code | **Yes** |
| Before pushing to `main` | **Yes** |
| Before running the `cicd` workflow by hand | **Yes** |
| After fixing a CI failure | **Yes** (re-run full gates) |

Agents: if you touched `backend/`, `auth/`, `storage/`, `practice/`, `content/`, `study/`, `library/`, `admin/`, or `frontend/`, run ship gates before telling
the user the work is done. Do not offer deploy until gates pass.

## One command

From the repo root:

```bash
./scripts/ship-gates.sh
```

Optional: pass a scope when you only changed one side:

```bash
./scripts/ship-gates.sh backend    # ruff + import check + unit tests
./scripts/ship-gates.sh auth       # ruff + import check + unit tests
./scripts/ship-gates.sh storage    # ruff + import check + unit tests
./scripts/ship-gates.sh practice   # ruff + import check + unit tests
./scripts/ship-gates.sh content    # ruff + import check + unit tests
./scripts/ship-gates.sh study      # ruff + import check + unit tests
./scripts/ship-gates.sh library    # ruff + import check + unit tests
./scripts/ship-gates.sh admin      # ruff + import check + unit tests
./scripts/ship-gates.sh frontend   # eslint + next build
```

## What it runs (CI parity)

### Backend

1. `python -m ruff check .` — unused imports, undefined names (`E9`, `F`)
2. Import smoke: no `app/main.py`; `from app.db import is_db_outage`
3. Worker import smoke: `from app.eta.worker import run_eta_worker` (and the IO
   worker) must not load `app.api` or a sibling `*_api` package
4. `python -m pytest tests/unit/ -q --tb=no`

### Auth

1. `python -m ruff check .` (same ruff set as backend)
2. Import smoke: `from app.main import app` (`Zivo Auth`)
3. `python -m pytest tests/unit/ -q --tb=no`

### Storage

1. `python -m ruff check .` (same ruff set as backend)
2. Import smoke: `from app.main import app` (`Zivo Storage`)
3. `python -m pytest tests/unit/ -q --tb=no`

### Practice / content / study / library / admin

Same three checks, with `PYTHONPATH=../backend:.` and import smoke on
`practice_main` / `content_main` / `study_main` / `library_main` / `admin_main`
that fails if the process loaded a sibling `*_api` package.

### Frontend

1. `npm run lint` — ESLint (core-web-vitals)
2. `npm run build` — TypeScript + Next.js compile

CI today: [`.github/workflows/cicd.yml`](../.github/workflows/cicd.yml) runs the
unit-test suites only; `npm run lint` and `npm run build` are enforced by ship
gates locally, not in CI.

## If a gate fails

1. Fix the reported issue; do not disable the linter.
2. Re-run `./scripts/ship-gates.sh`.
3. Only then commit, push, or deploy.

Common fixes:

- **Ruff F401** — remove unused imports (`ruff check --fix .` is safe for F401).
- **ESLint errors** — fix the rule violation; warnings are OK for now.
- **Build / type errors** — fix types or imports; Next build is strict.

## Deploy pipeline

The [`cicd`](../.github/workflows/cicd.yml) pipeline unit-tests, builds and pushes
all service images to GHCR, and on `main` waits for the `production` environment
approval gate before promoting the new tag into the prod helm values for ArgoCD
to roll out. Agents following
[`production-release-deploy`](../.cursor/skills/production-release-deploy/SKILL.md)
must run ship gates (or confirm CI green on the target SHA) before pushing to
`main`.

## Holy grail tie-in

Engines own policy; plumbing owns I/O. Ship gates are plumbing: they do not decide
product behavior, they stop broken code from reaching prod. **Code should run, not
the coder** — run the gates, fix failures, then ship.
