# Public content is the fourth HTTP microservice

**Date:** 2026-08-13 · **Status:** accepted

Zivo splits SEO `/api/learn` and newspaper ingest-admin HTTP out of the product
API into `content/` (`content.zivo.fyi`, Helm `zivo-content`, local `:8204`).
Newspaper Telethon ingest stays on the existing worker chart.

**Why.** Public posts and admin knobs are not the study loop. Same process +
hostname + `alembic_version_content` + exclusive `content` schema marker bar as
practice ([ADR 0010](0010-practice-microservice.md)).

**Data.** `qb.seo_*` and newspaper allowlist rows stay on product Alembic.
Workers cook posts and editions. Content is the only HTTP writer for learn-blog
and `/api/newspaper/admin`.

**Not in content.** Practice catalog (`/api/newspaper` without admin). MCQ grade
(study). Telethon worker.

**Consequence.** DNS needs a `content.zivo.fyi` A record before TLS. Until then
apex rewrites `/api/learn` and `/api/newspaper/admin` to in-cluster
`zivo-content`. RSC fetches must use `CONTENT_PROXY_URL`, not `API_PROXY_URL`.
