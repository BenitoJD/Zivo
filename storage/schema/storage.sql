-- Storage object metadata. Bytes live in MinIO; this schema is the write-owner for keys.
CREATE SCHEMA IF NOT EXISTS storage;

CREATE TABLE IF NOT EXISTS storage.object (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  storage_key     TEXT NOT NULL UNIQUE,
  account_id      UUID,
  guest_id        VARCHAR(32),
  filename        VARCHAR(512) NOT NULL,
  content_type    VARCHAR(128) NOT NULL,
  size_bytes      BIGINT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS object_account_idx ON storage.object (account_id);
CREATE INDEX IF NOT EXISTS object_guest_idx ON storage.object (guest_id);

CREATE TABLE IF NOT EXISTS storage.upload_session (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id           UUID,
  guest_id             VARCHAR(32),
  filename             VARCHAR(512) NOT NULL,
  content_type         VARCHAR(128) NOT NULL,
  total_size           BIGINT NOT NULL,
  chunk_size           INTEGER NOT NULL,
  storage_key          TEXT NOT NULL,
  multipart_upload_id  TEXT NOT NULL,
  parts                JSONB NOT NULL DEFAULT '{}'::jsonb,
  expires_at           TIMESTAMPTZ NOT NULL,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS storage.rate_limit_hit (
  bucket      INTEGER NOT NULL,
  client_key  TEXT NOT NULL,
  hit_count   INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (bucket, client_key)
);
