-- Question Better app schema (additive; intel_foundation.sql unchanged)
CREATE SCHEMA IF NOT EXISTS qb;

-- -----------------------------------------------------------------------------
-- Auth
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qb.account (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  username        VARCHAR(32) NOT NULL UNIQUE,
  password_hash   VARCHAR(255) NOT NULL,
  is_admin        BOOLEAN NOT NULL DEFAULT false,
  session_version INTEGER NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS account_username_idx ON qb.account (username);

-- Learn progress (promoted from documents.meta.question_progress)
CREATE TABLE IF NOT EXISTS qb.document_learn_state (
  document_id UUID PRIMARY KEY REFERENCES qb.documents (id) ON DELETE CASCADE,
  progress    JSONB NOT NULL DEFAULT '{}',
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Hot MCQ fields indexed outside intel.assertion.payload
CREATE TABLE IF NOT EXISTS qb.mcq_assertion_facets (
  assertion_id  UUID PRIMARY KEY,
  artifact_id   UUID NOT NULL,
  page_number   INTEGER NOT NULL,
  sequence      INTEGER NOT NULL,
  question      TEXT,
  correct_index INTEGER,
  CONSTRAINT mcq_assertion_facets_assertion_fk
    FOREIGN KEY (assertion_id) REFERENCES intel.assertion (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_mcq_facets_artifact_page_seq
  ON qb.mcq_assertion_facets (artifact_id, page_number, sequence);

CREATE INDEX IF NOT EXISTS ix_mcq_facets_artifact_page
  ON qb.mcq_assertion_facets (artifact_id, page_number);

CREATE TABLE IF NOT EXISTS qb.upload_session (
  id UUID PRIMARY KEY,
  account_id UUID REFERENCES qb.account (id) ON DELETE CASCADE,
  guest_id TEXT,
  filename TEXT NOT NULL,
  content_type TEXT NOT NULL,
  total_size BIGINT NOT NULL,
  chunk_size INTEGER NOT NULL,
  storage_key TEXT NOT NULL,
  multipart_upload_id TEXT NOT NULL,
  parts JSONB NOT NULL DEFAULT '{}',
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_upload_session_expires ON qb.upload_session (expires_at);

-- Links qb.account -> intel.entity (person) for measurement / projection
CREATE TABLE IF NOT EXISTS qb.account_entity (
  account_id UUID PRIMARY KEY REFERENCES qb.account (id) ON DELETE CASCADE,
  entity_id  UUID NOT NULL REFERENCES intel.entity (id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- Chat (tutor)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qb.chat_thread (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id           UUID REFERENCES qb.account (id) ON DELETE SET NULL,
  artifact_id          UUID NOT NULL,
  artifact_captured_at TIMESTAMPTZ NOT NULL,
  version              INTEGER NOT NULL DEFAULT 1,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_chat_thread_version UNIQUE (account_id, artifact_id, artifact_captured_at, version)
);

CREATE INDEX IF NOT EXISTS chat_thread_artifact_idx
  ON qb.chat_thread (artifact_id, artifact_captured_at);

CREATE INDEX IF NOT EXISTS chat_thread_account_idx
  ON qb.chat_thread (account_id);

CREATE TABLE IF NOT EXISTS qb.chat_message (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  thread_id  UUID NOT NULL REFERENCES qb.chat_thread (id) ON DELETE CASCADE,
  role       VARCHAR(16) NOT NULL,
  content    TEXT NOT NULL,
  citations  JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chat_message_thread_idx ON qb.chat_message (thread_id, created_at);

-- -----------------------------------------------------------------------------
-- Workspace preferences + mutable artifact state (intel.artifact is INSERT-only)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qb.workspace_preference (
  account_id UUID PRIMARY KEY REFERENCES qb.account (id) ON DELETE CASCADE,
  mode       TEXT NOT NULL DEFAULT 'learn',
  mastery_strictness TEXT NOT NULL DEFAULT 'standard',
  pool_size  INTEGER NOT NULL DEFAULT 5,
  panel_layout JSONB NOT NULL DEFAULT '{}',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS qb.artifact_workspace (
  account_id           UUID NOT NULL REFERENCES qb.account (id) ON DELETE CASCADE,
  artifact_id          UUID NOT NULL,
  artifact_captured_at TIMESTAMPTZ NOT NULL,
  status               TEXT NOT NULL DEFAULT 'pending_page_selection',
  page_count           INTEGER,
  selected_range       JSONB,
  current_page         INTEGER,
  unlocked_through_page INTEGER,
  pool_target          INTEGER NOT NULL DEFAULT 5,
  pool_available_count INTEGER NOT NULL DEFAULT 0,
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (account_id, artifact_id, artifact_captured_at)
);

CREATE INDEX IF NOT EXISTS artifact_workspace_status_idx ON qb.artifact_workspace (status);

CREATE TABLE IF NOT EXISTS qb.question_feedback (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  assertion_id UUID NOT NULL,
  account_id   UUID REFERENCES qb.account (id) ON DELETE SET NULL,
  reason       TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS question_feedback_assertion_idx ON qb.question_feedback (assertion_id);
