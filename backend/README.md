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
│   └── intel_foundation.sql   # legacy scaffold — question graph DDL next
└── scripts/
    └── apply_intel_schema.py
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
./scripts/dev.sh db schema
```

Idempotent in production via the `db-schema` Helm job (`scripts/run-k8s-schema-migrate.sh`).

## Docker

```bash
docker build -t zivo-api ./backend
```

Image target: `runtime` (used by CI → GHCR → K3s).
