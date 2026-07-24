-- Question Better app schema (additive; intel_foundation.sql unchanged)
CREATE SCHEMA IF NOT EXISTS qb;

-- -----------------------------------------------------------------------------
-- Auth
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qb.account (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  username        VARCHAR(32) NOT NULL UNIQUE,
  password_hash   VARCHAR(255),
  email           VARCHAR(320),
  google_sub      VARCHAR(64),
  is_admin        BOOLEAN NOT NULL DEFAULT false,
  session_version INTEGER NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS account_username_idx ON qb.account (username);
CREATE UNIQUE INDEX IF NOT EXISTS account_email_uidx
  ON qb.account (email) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS account_google_sub_uidx
  ON qb.account (google_sub) WHERE google_sub IS NOT NULL;

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

-- -----------------------------------------------------------------------------
-- System Design mastery (practice destination — not Interview)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qb.sd_concept (
  key            TEXT PRIMARY KEY,
  title          TEXT NOT NULL,
  blurb          TEXT NOT NULL DEFAULT '',
  sort_order     INTEGER NOT NULL DEFAULT 0,
  prerequisites  TEXT[] NOT NULL DEFAULT '{}',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS qb.sd_problem (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug              TEXT NOT NULL UNIQUE,
  title             TEXT NOT NULL,
  prompt            TEXT NOT NULL,
  constraints       TEXT NOT NULL DEFAULT '',
  difficulty        TEXT NOT NULL DEFAULT 'medium',
  concept_keys      TEXT[] NOT NULL DEFAULT '{}',
  rubric_hints      JSONB NOT NULL DEFAULT '{}'::jsonb,
  reference_design  TEXT NOT NULL DEFAULT '',
  published         BOOLEAN NOT NULL DEFAULT true,
  sort_order        INTEGER NOT NULL DEFAULT 0,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT sd_problem_difficulty_check
    CHECK (difficulty IN ('easy', 'medium', 'hard'))
);

CREATE INDEX IF NOT EXISTS ix_sd_problem_published
  ON qb.sd_problem (published, sort_order)
  WHERE published = true;

CREATE INDEX IF NOT EXISTS ix_sd_problem_concepts
  ON qb.sd_problem USING GIN (concept_keys);

CREATE TABLE IF NOT EXISTS qb.sd_session (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  problem_id            UUID NOT NULL REFERENCES qb.sd_problem (id) ON DELETE CASCADE,
  account_id            UUID REFERENCES qb.account (id) ON DELETE SET NULL,
  guest_id              TEXT,
  status                TEXT NOT NULL DEFAULT 'active',
  design                JSONB NOT NULL DEFAULT '{}'::jsonb,
  scores                JSONB NOT NULL DEFAULT '{}'::jsonb,
  feedback              JSONB NOT NULL DEFAULT '{}'::jsonb,
  weak_concepts         TEXT[] NOT NULL DEFAULT '{}',
  lesson                JSONB NOT NULL DEFAULT '{}'::jsonb,
  recommended_next_id   UUID REFERENCES qb.sd_problem (id) ON DELETE SET NULL,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT sd_session_status_check
    CHECK (status IN ('active', 'done')),
  CONSTRAINT sd_session_subject_check
    CHECK (account_id IS NOT NULL OR guest_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_sd_session_account_active
  ON qb.sd_session (account_id, status, updated_at DESC)
  WHERE account_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_sd_session_guest_active
  ON qb.sd_session (guest_id, status, updated_at DESC)
  WHERE guest_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- SEO /learn (see qb_infra.sql + Alembic 032_seo_learn_content for full DDL)
-- Posts FK qb.documents; authored mirror lives with infra newspaper tables.
-- -----------------------------------------------------------------------------
