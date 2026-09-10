# Question Better. — backend

Shared product library (engines, models, services) and Alembic for `intel.*` /
`qb.*`. HTTP lives in `practice/`, `content/`, `study/`, `library/`, and `admin/`.

## Status

| Piece | Ready? |
|-------|--------|
| Shared health helper | Yes (`app/api/health.py`) |
| Config / DB session | Yes |
| Postgres + pgvector | Yes |
| Product Alembic | Yes (`zivo-migrate` image) |

## Layout

```
backend/
├── app/
│   ├── api/              # shared health helper (not a process)
│   ├── workers/          # ingest / normalize job helpers
│   ├── config.py
│   └── db.py
├── schema/
│   ├── intel_foundation.sql
│   ├── qb_app.sql
│   └── qb_infra.sql
├── alembic/
│   └── versions/
└── scripts/
    ├── run_alembic_with_lock.py
    └── test_alembic_migrations.sh
```

## Run locally

From repo root:

```bash
./scripts/dev.sh setup
./scripts/dev.sh start
curl http://127.0.0.1:8201/health
```

## Schema

```bash
./scripts/dev.sh db migrate
./scripts/dev.sh db seed
```

Production: `alembic-migrate` K8s Job via `zivo-migrate` — run manually with `scripts/run-k8s-schema-migrate.sh` (admin kubeconfig). The `cicd` pipeline builds the image and promotes the tag; ArgoCD rolls out.

## Docker

```bash
docker build -t zivo-migrate -f backend/Dockerfile --target runtime backend/
docker build -t zivo-worker -f workers/Dockerfile .
```

Image target: `runtime` (used by CI → GHCR → K3s).
