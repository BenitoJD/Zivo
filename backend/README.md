# Question Better. — backend

FastAPI service and workers for the question engine.

## Status

| Piece | Ready? |
|-------|--------|
| Health endpoints | Yes |
| Config / DB session | Yes |
| Postgres + pgvector | Yes |
| Source upload storage (MinIO) | Infra ready |
| Question generation | **Next** |
| Question evaluation | Planned |
| Answer capture + calibration | Planned |
| Question graph schema | Planned |

## Layout

```
backend/
├── app/
│   ├── api/              # HTTP routes — generation, quiz, analytics
│   ├── workers/          # async generation, embedding, eval jobs
│   ├── config.py
│   ├── db.py
│   └── main.py
├── schema/
│   ├── intel_foundation.sql   # intel DDL (Alembic 001)
│   ├── qb_app.sql             # qb app DDL (Alembic 002)
│   └── qb_infra.sql           # qb infra DDL (Alembic 002)
├── alembic/
│   └── versions/              # migration chain
└── scripts/
    ├── run_alembic_with_lock.py
    └── test_alembic_migrations.sh
```

## Run locally

From repo root:

```bash
./scripts/dev.sh setup
./scripts/dev.sh start
curl http://127.0.0.1:8200/health/ready
```

## Schema

```bash
./scripts/dev.sh db migrate
./scripts/dev.sh db seed
```

Production: `alembic-migrate` K8s Job via `scripts/run-k8s-schema-migrate.sh`.

## Docker

```bash
docker build -t zivo-api ./backend
```

Image target: `runtime` (used by CI → GHCR → K3s).
