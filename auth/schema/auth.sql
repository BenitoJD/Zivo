-- Auth identity schema (credentials). Product FKs stay on qb.account.
CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE IF NOT EXISTS auth.account (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  username        VARCHAR(32) NOT NULL UNIQUE,
  password_hash   VARCHAR(255),
  email           VARCHAR(320),
  google_sub      VARCHAR(64),
  is_admin        BOOLEAN NOT NULL DEFAULT false,
  session_version INTEGER NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS account_username_idx ON auth.account (username);
CREATE UNIQUE INDEX IF NOT EXISTS account_email_uidx
  ON auth.account (email) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS account_google_sub_uidx
  ON auth.account (google_sub) WHERE google_sub IS NOT NULL;

CREATE TABLE IF NOT EXISTS auth.rate_limit_hit (
  bucket      INTEGER NOT NULL,
  client_key  TEXT NOT NULL,
  hit_count   INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (bucket, client_key)
);
