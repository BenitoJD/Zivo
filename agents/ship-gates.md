# Ship gates (reach gold before main)

**Gold** = the same checks CI runs on `main`, plus frontend lint. A change is not
ready to commit, merge, or deploy until ship gates pass locally.

Deploy does **not** re-run lint. If you skip gates locally, CI fails on push and
the Docker build can fail on type or compile errors. Run gates **before** you
commit or trigger **Deploy Zivo**.

## When to run

| Moment | Required? |
|--------|-----------|
| Before committing backend or frontend code | **Yes** |
| Before pushing to `main` | **Yes** |
| Before `gh workflow run deploy.yml` | **Yes** |
| After fixing a CI failure | **Yes** (re-run full gates) |

Agents: if you touched `backend/`, `auth/`, `storage/`, `practice/`, `content/`, `study/`, or `frontend/`, run ship gates before telling
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
./scripts/ship-gates.sh frontend   # eslint + next build
```

## What it runs (CI parity)

### Backend

1. `python -m ruff check .` — unused imports, undefined names (`E9`, `F`)
2. Import smoke: `from app.main import app`
3. `python -m pytest tests/unit/ -q --tb=no`

### Auth

1. `python -m ruff check .` (same ruff set as backend)
2. Import smoke: `from app.main import app` (`Zivo Auth`)
3. `python -m pytest tests/unit/ -q --tb=no`

### Storage

1. `python -m ruff check .` (same ruff set as backend)
2. Import smoke: `from app.main import app` (`Zivo Storage`)
3. `python -m pytest tests/unit/ -q --tb=no`

### Practice / content / study

Same three checks, with `PYTHONPATH=../backend:.` and import smoke on
`practice_main` / `content_main` / `study_main`.

### Frontend

1. `npm run lint` — ESLint (core-web-vitals)
2. `npm run build` — TypeScript + Next.js compile

CI today: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml). Frontend
`npm run lint` is in ship gates even though CI still only runs `npm run build`
(see [ADR 0005](../docs/adr/0005-enforcement-and-known-divergences.md)).

## If a gate fails

1. Fix the reported issue; do not disable the linter.
2. Re-run `./scripts/ship-gates.sh`.
3. Only then commit, push, or deploy.

Common fixes:

- **Ruff F401** — remove unused imports (`ruff check --fix .` is safe for F401).
- **ESLint errors** — fix the rule violation; warnings are OK for now.
- **Build / type errors** — fix types or imports; Next build is strict.

## Deploy workflow

Production deploy ([`deploy.yml`](../.github/workflows/deploy.yml)) builds Docker
images and rolls out to K3s. It assumes `main` already passed CI. Agents following
[`production-release-deploy`](../.cursor/skills/production-release-deploy/SKILL.md)
must run ship gates (or confirm CI green on the target SHA) before dispatching
deploy.

## Holy grail tie-in

Engines own policy; plumbing owns I/O. Ship gates are plumbing: they do not decide
product behavior, they stop broken code from reaching prod. **Code should run, not
the coder** — run the gates, fix failures, then ship.
