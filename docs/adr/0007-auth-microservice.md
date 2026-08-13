# Identity is the first microservice

**Date:** 2026-08-13 · **Status:** accepted

Zivo splits identity out of the product API into a separately deployed FastAPI
service (`auth/`, `auth.zivo.fyi`, Helm chart `zivo-auth`). Signup, login,
logout, session, and Google OAuth write `auth.account`. The product API only
verifies the existing `zivo_session` cookie (JWT + `session_version`) and keeps
`qb.account` as a stub that every product FK still points at.

**Why.** This is the first slice of a gradual conversion, not a module rename.
A dedicated process, hostname, and write-owner for credentials lets later
services verify the same cookie the same way without retargeting every
`account_id` FK in this slice.

**Data.** Same Postgres, new `auth` schema. Auth Alembic (`alembic_version_auth`)
owns `auth.*`. Backend Alembic copies `qb.account` → `auth.account` then drops
secret columns from the stub (`045_auth_account_split`). Not a second database.

**Cookie.** Production `Domain=zivo.fyi` so apex, `www`, `auth`, and `api` are
same-site. `SameSite=Lax` + `credentials: include`. Shared `SECRET_KEY` (HS256)
so the product API does not HTTP-introspect; that key also HMAC-signs offline
packs ([ADR 0006](0006-offline-answer-keys.md)). Do not rotate it as auth-only
until packs get their own signing key.

**Not in auth.** Guest mint (`POST /api/guest`) and guest claim stay on the
product API. Auth never imports documents, progress, or intel.

**Considered and rejected.**

- *Separate Postgres instance.* Extra ops for no isolation win in this slice.
- *HTTP introspect / JWKS.* Needed when a third service appears; shared secret
  is enough for two.
- *Retarget every FK at `auth.account`.* Hard to reverse and out of scope.
- *Shared Python JWT package.* Would become a distributed monolith; duplicate
  the small decode instead.

**Consequence.** Product `get_current_user` must keep reading
`auth.account.session_version` on each authenticated request or a stolen cookie
survives until expiry. Google Console redirect URI must be
`https://auth.zivo.fyi/api/auth/google/callback` in the same deploy as the
hostname. DNS needs an `auth.zivo.fyi` A record to the VPS.
