# Tech stack

| Layer | Choice |
|-------|--------|
| API | FastAPI, SQLAlchemy, psycopg |
| Workers | Citepage ETA (IO + CPU), LangGraph agents |
| DB | PostgreSQL 16 + pgvector — `intel.*` product + `qb.*` app/ops |
| Object storage | MinIO (S3) |
| UI | Next.js 16 App Router, mobile-first workspace |
| LLM | LiteLLM registry in `qb.llm_*` tables |

## Job status

- **User-visible:** `intel.activity` — UI polls `/api/activities/*` only
- **Worker internals:** `qb.jobs`, `qb.eta_*` — linked via `activity_id`

## Schemas

- `intel_foundation.sql` — intel DDL (applied by Alembic `001_intel_foundation`)
- `qb_app.sql` / `qb_infra.sql` — qb DDL (applied by Alembic `002_qb_schema`)
- **Migrations:** Alembic (`backend/alembic/versions/`); prod Job `alembic-migrate`
