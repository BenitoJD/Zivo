-- Question Better ops / worker / LLM infra (additive)
CREATE SCHEMA IF NOT EXISTS qb;

DO $$ BEGIN
  CREATE TYPE qb.jobpriority AS ENUM ('LOW', 'MEDIUM', 'HIGH');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE qb.etaexecutionmode AS ENUM ('group', 'dag');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE qb.etaexecutionstatus AS ENUM (
    'queued', 'running', 'succeeded', 'partial_failed', 'failed', 'cancelled'
  );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- System prompts (ported from the platform template)
CREATE TABLE IF NOT EXISTS qb.system_prompts (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  key        VARCHAR(64) UNIQUE NOT NULL,
  content    TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS system_prompts_key_idx ON qb.system_prompts (key);

-- Worker job queue
CREATE TABLE IF NOT EXISTS qb.jobs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name         VARCHAR(128) NOT NULL,
  status       VARCHAR(16) NOT NULL DEFAULT 'queued',
  workload     VARCHAR(8) NOT NULL DEFAULT 'io',
  priority     qb.jobpriority NOT NULL DEFAULT 'MEDIUM',
  account_id   UUID REFERENCES qb.account (id) ON DELETE SET NULL,
  activity_id  UUID REFERENCES intel.activity (id) ON DELETE SET NULL,
  execution_id UUID,
  node_key     VARCHAR(128),
  payload      JSONB NOT NULL DEFAULT '{}',
  result       JSONB,
  error        TEXT,
  attempts     INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  locked_by    VARCHAR(64),
  locked_at    TIMESTAMPTZ,
    run_after    TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS jobs_status_idx ON qb.jobs (status);
CREATE INDEX IF NOT EXISTS jobs_workload_idx ON qb.jobs (workload);
CREATE INDEX IF NOT EXISTS jobs_priority_idx ON qb.jobs (priority);
CREATE INDEX IF NOT EXISTS jobs_account_idx ON qb.jobs (account_id);
CREATE INDEX IF NOT EXISTS jobs_activity_idx ON qb.jobs (activity_id);
CREATE INDEX IF NOT EXISTS jobs_execution_idx ON qb.jobs (execution_id);
CREATE INDEX IF NOT EXISTS ix_jobs_queued_pick
  ON qb.jobs (workload, priority DESC, created_at)
  WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS ix_jobs_payload_document_id
  ON qb.jobs ((payload->>'document_id'));
CREATE INDEX IF NOT EXISTS ix_jobs_terminal_finished
  ON qb.jobs (finished_at)
  WHERE status IN ('succeeded', 'failed', 'cancelled');
CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_execution_node_key
  ON qb.jobs (execution_id, node_key) WHERE execution_id IS NOT NULL AND node_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS qb.eta_executions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            VARCHAR(255) NOT NULL,
  mode            qb.etaexecutionmode NOT NULL,
  status          qb.etaexecutionstatus NOT NULL,
  priority        qb.jobpriority NOT NULL,
  account_id      UUID REFERENCES qb.account (id) ON DELETE SET NULL,
  total_jobs      INTEGER NOT NULL DEFAULT 0,
  queued_jobs     INTEGER NOT NULL DEFAULT 0,
  running_jobs    INTEGER NOT NULL DEFAULT 0,
  succeeded_jobs  INTEGER NOT NULL DEFAULT 0,
  failed_jobs     INTEGER NOT NULL DEFAULT 0,
  cancelled_jobs  INTEGER NOT NULL DEFAULT 0,
  error           TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS eta_executions_name_idx ON qb.eta_executions (name);
CREATE INDEX IF NOT EXISTS eta_executions_status_idx ON qb.eta_executions (status);

ALTER TABLE qb.jobs
  DROP CONSTRAINT IF EXISTS jobs_execution_id_fkey;
ALTER TABLE qb.jobs
  ADD CONSTRAINT jobs_execution_id_fkey
  FOREIGN KEY (execution_id) REFERENCES qb.eta_executions (id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS qb.eta_job_dependencies (
  job_id             UUID NOT NULL REFERENCES qb.jobs (id) ON DELETE CASCADE,
  depends_on_job_id  UUID NOT NULL REFERENCES qb.jobs (id) ON DELETE CASCADE,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (job_id, depends_on_job_id),
  CONSTRAINT ck_eta_job_dependencies_no_self CHECK (job_id <> depends_on_job_id)
);

CREATE INDEX IF NOT EXISTS eta_job_dependencies_depends_idx
  ON qb.eta_job_dependencies (depends_on_job_id);

CREATE TABLE IF NOT EXISTS qb.eta_schedules (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  schedule_key TEXT NOT NULL UNIQUE,
  enabled      BOOLEAN NOT NULL DEFAULT true,
  is_orphaned  BOOLEAN NOT NULL DEFAULT false,
  next_run_at  TIMESTAMPTZ NOT NULL,
  last_run_at  TIMESTAMPTZ,
  last_error   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS eta_schedules_next_run_idx ON qb.eta_schedules (next_run_at);

-- LLM registry
CREATE TABLE IF NOT EXISTS qb.llm_providers (
  id              UUID PRIMARY KEY,
  slug            VARCHAR(64) NOT NULL UNIQUE,
  display_name    VARCHAR(128) NOT NULL,
  litellm_prefix  VARCHAR(64) NOT NULL,
  api_base_url    VARCHAR(512),
  api_key         TEXT,
  extra_env       JSONB NOT NULL DEFAULT '{}',
  is_enabled      BOOLEAN NOT NULL DEFAULT true,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS qb.llm_models (
  id                   UUID PRIMARY KEY,
  provider_id          UUID NOT NULL REFERENCES qb.llm_providers (id),
  slug                 VARCHAR(128) NOT NULL,
  litellm_model        VARCHAR(256) NOT NULL,
  display_name         VARCHAR(128) NOT NULL,
  kind                 VARCHAR(32) NOT NULL DEFAULT 'chat',
  supports_text_input  BOOLEAN NOT NULL DEFAULT true,
  supports_image_input BOOLEAN NOT NULL DEFAULT false,
  supports_text_output BOOLEAN NOT NULL DEFAULT true,
  supports_streaming   BOOLEAN NOT NULL DEFAULT true,
  supports_tools       BOOLEAN NOT NULL DEFAULT false,
  max_input_tokens     INTEGER,
  max_output_tokens    INTEGER,
  is_enabled           BOOLEAN NOT NULL DEFAULT true,
  is_default           BOOLEAN NOT NULL DEFAULT false,
  sort_order           INTEGER NOT NULL DEFAULT 0,
  meta                 JSONB NOT NULL DEFAULT '{}',
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_llm_models_provider_slug UNIQUE (provider_id, slug)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_models_default_per_kind
  ON qb.llm_models (kind) WHERE is_default IS TRUE;

-- Semantic response cache (artifact-scoped)
CREATE TABLE IF NOT EXISTS qb.llm_response_cache (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  artifact_id          UUID NOT NULL,
  artifact_captured_at TIMESTAMPTZ NOT NULL,
  scope_hash           VARCHAR(64) NOT NULL,
  response_text        TEXT NOT NULL,
  citations            JSONB NOT NULL DEFAULT '{}',
  hit_count            INTEGER NOT NULL DEFAULT 0,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS llm_response_cache_artifact_scope_idx
  ON qb.llm_response_cache (artifact_id, artifact_captured_at, scope_hash);

DO $$ BEGIN
  ALTER TABLE qb.llm_response_cache ADD COLUMN embedding vector(384);
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS ix_llm_response_cache_embedding_hnsw
  ON qb.llm_response_cache USING hnsw (embedding vector_cosine_ops)
  WHERE embedding IS NOT NULL;

-- intel.embedding uses variable-dimension vectors; per-model HNSW indexes belong in a migration
-- once dimensions are fixed per model_concept_id.
CREATE TABLE IF NOT EXISTS qb.usage_daily (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id    UUID REFERENCES qb.account (id) ON DELETE CASCADE,
  usage_date    DATE NOT NULL,
  message_count INTEGER NOT NULL DEFAULT 0,
  CONSTRAINT uq_usage_daily UNIQUE (account_id, usage_date)
);

CREATE TABLE IF NOT EXISTS qb.demo_usage (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ip_hash       VARCHAR(64) NOT NULL,
  cookie_id     VARCHAR(64) NOT NULL,
  usage_date    DATE NOT NULL,
  message_count INTEGER NOT NULL DEFAULT 0,
  CONSTRAINT uq_demo_usage UNIQUE (ip_hash, cookie_id, usage_date)
);

CREATE TABLE IF NOT EXISTS qb.llm_usage_event (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tag               VARCHAR(64) NOT NULL,
  model             VARCHAR(256) NOT NULL,
  prompt_tokens     INT NOT NULL DEFAULT 0,
  completion_tokens INT NOT NULL DEFAULT 0,
  cached_tokens     INT NOT NULL DEFAULT 0,
  latency_ms        INT NOT NULL DEFAULT 0,
  account_id        UUID REFERENCES qb.account (id) ON DELETE SET NULL,
  document_id       UUID,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS llm_usage_event_created_at_idx
  ON qb.llm_usage_event (created_at DESC);

CREATE INDEX IF NOT EXISTS llm_usage_event_document_idx
  ON qb.llm_usage_event (document_id, created_at DESC)
  WHERE document_id IS NOT NULL;

-- Document document/chunk tables (ORM port layer; canonical evidence in intel.artifact)
CREATE TABLE IF NOT EXISTS qb.documents (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id     UUID REFERENCES qb.account (id) ON DELETE SET NULL,
  slug           VARCHAR(64) NOT NULL,
  filename       VARCHAR(512) NOT NULL,
  content_type   VARCHAR(128) NOT NULL,
  size_bytes     INTEGER NOT NULL,
  storage_key    VARCHAR(1024) NOT NULL,
  status         VARCHAR(32) NOT NULL DEFAULT 'pending',
  index_progress INTEGER NOT NULL DEFAULT 0,
  vision_opt_out BOOLEAN NOT NULL DEFAULT false,
  meta           JSONB NOT NULL DEFAULT '{}',
  artifact_id          UUID,
  artifact_captured_at TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS documents_account_idx ON qb.documents (account_id);
CREATE INDEX IF NOT EXISTS documents_slug_idx ON qb.documents (slug);
CREATE INDEX IF NOT EXISTS documents_artifact_idx ON qb.documents (artifact_id, artifact_captured_at);
CREATE INDEX IF NOT EXISTS documents_guest_id_idx
  ON qb.documents ((meta->>'guest_id'))
  WHERE account_id IS NULL;

CREATE TABLE IF NOT EXISTS qb.document_chunks (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID NOT NULL REFERENCES qb.documents (id) ON DELETE CASCADE,
  page_start  INTEGER NOT NULL,
  page_end    INTEGER NOT NULL,
  text        TEXT NOT NULL,
  meta        JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS document_chunks_document_idx ON qb.document_chunks (document_id);

DO $$ BEGIN
  ALTER TABLE qb.document_chunks ADD COLUMN embedding vector(384);
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS document_chunks_embedding_hnsw_idx
  ON qb.document_chunks USING hnsw (embedding vector_cosine_ops)
  WHERE embedding IS NOT NULL;

-- -----------------------------------------------------------------------------
-- Newspaper practice (shared editions — MCQs only for learners)
-- Lives here (not qb_app.sql) because editions FK to qb.documents above.
-- Additive migration: 028_newspaper.py
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qb.newspaper_settings (
  id              INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  channel_ref     TEXT NOT NULL DEFAULT '',
  channel_label   TEXT NOT NULL DEFAULT '',
  sync_cursor     BIGINT,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO qb.newspaper_settings (id, channel_ref, channel_label)
VALUES (1, '', '')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS qb.newspaper_paper_alias (
  alias_key       TEXT PRIMARY KEY,
  paper_slug      TEXT NOT NULL,
  paper_title     TEXT NOT NULL,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS qb.newspaper_edition (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  paper_slug        TEXT NOT NULL,
  paper_title       TEXT NOT NULL,
  edition_date      DATE NOT NULL,
  document_id       UUID REFERENCES qb.documents (id) ON DELETE SET NULL,
  telegram_msg_id   BIGINT,
  location_raw      TEXT NOT NULL DEFAULT '',
  status            TEXT NOT NULL DEFAULT 'pending',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT newspaper_edition_status_check
    CHECK (status IN ('pending', 'indexing', 'ready', 'failed', 'purged')),
  CONSTRAINT newspaper_edition_paper_day_unique
    UNIQUE (paper_slug, edition_date)
);

CREATE INDEX IF NOT EXISTS ix_newspaper_edition_ready_date
  ON qb.newspaper_edition (edition_date DESC)
  WHERE status = 'ready';

CREATE INDEX IF NOT EXISTS ix_newspaper_edition_paper
  ON qb.newspaper_edition (paper_slug, edition_date DESC);

-- Newspaper brand allowlist (additive; also 029_newspaper_brands)
ALTER TABLE qb.newspaper_settings
  ADD COLUMN IF NOT EXISTS allowlist_only BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS qb.newspaper_brand (
  paper_slug     TEXT PRIMARY KEY,
  paper_title    TEXT NOT NULL,
  enabled        BOOLEAN NOT NULL DEFAULT false,
  first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_newspaper_brand_enabled
  ON qb.newspaper_brand (enabled)
  WHERE enabled = true;

-- Hindu v1 seed (also 031_hindu_newspaper_v1).
-- Paper aliases are NOT seeded — ingest LLM classifies filenames and learns
-- into qb.newspaper_paper_alias (names drift; avoid hardcoding TH/ET/…).
UPDATE qb.newspaper_settings
  SET allowlist_only = true, updated_at = now()
  WHERE id = 1;

INSERT INTO qb.newspaper_brand (paper_slug, paper_title, enabled, first_seen_at, updated_at)
VALUES ('the-hindu', 'The Hindu', true, now(), now())
ON CONFLICT (paper_slug) DO UPDATE SET
  paper_title = EXCLUDED.paper_title,
  enabled = true,
  updated_at = now();

-- -----------------------------------------------------------------------------
-- SEO /learn content engine (additive; also 032_seo_learn_content)
-- Public posts; source_ref is internal-only. cook_enabled defaults false.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qb.seo_settings (
  id                INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  cook_enabled      BOOLEAN NOT NULL DEFAULT false,
  soft_max_per_day  INTEGER NOT NULL DEFAULT 20,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO qb.seo_settings (id, cook_enabled, soft_max_per_day)
VALUES (1, false, 20)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS qb.seo_author (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL UNIQUE,
  active      BOOLEAN NOT NULL DEFAULT true,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS qb.seo_post (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug                TEXT NOT NULL,
  title               TEXT NOT NULL,
  lede                TEXT NOT NULL DEFAULT '',
  body_md             TEXT NOT NULL DEFAULT '',
  format              TEXT NOT NULL DEFAULT 'explainer',
  stream              TEXT NOT NULL DEFAULT 'general',
  author_name         TEXT NOT NULL DEFAULT '',
  status              TEXT NOT NULL DEFAULT 'draft',
  topic_fingerprint   TEXT NOT NULL,
  embedding           vector(384),
  source_kind         TEXT NOT NULL,
  source_ref          JSONB NOT NULL DEFAULT '{}'::jsonb,
  faq_jsonld          JSONB NOT NULL DEFAULT '[]'::jsonb,
  cta_kind            TEXT NOT NULL DEFAULT 'practice',
  artifact_id         UUID REFERENCES qb.documents (id) ON DELETE SET NULL,
  published_at        TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT seo_post_format_check
    CHECK (format IN ('explainer', 'faq', 'list')),
  CONSTRAINT seo_post_stream_check
    CHECK (stream IN ('general', 'system_design')),
  CONSTRAINT seo_post_status_check
    CHECK (status IN ('draft', 'published', 'unpublished')),
  CONSTRAINT seo_post_source_kind_check
    CHECK (source_kind IN ('newspaper', 'upload', 'sd_bank', 'topic_queue'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_seo_post_slug ON qb.seo_post (slug);
CREATE UNIQUE INDEX IF NOT EXISTS ux_seo_post_topic_fingerprint_published
  ON qb.seo_post (topic_fingerprint) WHERE status = 'published';
CREATE INDEX IF NOT EXISTS ix_seo_post_published_stream
  ON qb.seo_post (stream, published_at DESC) WHERE status = 'published';
CREATE INDEX IF NOT EXISTS ix_seo_post_published_at
  ON qb.seo_post (published_at DESC) WHERE status = 'published';
CREATE INDEX IF NOT EXISTS ix_seo_post_embedding_hnsw
  ON qb.seo_post USING hnsw (embedding vector_cosine_ops)
  WHERE embedding IS NOT NULL AND status = 'published';

CREATE TABLE IF NOT EXISTS qb.seo_post_assertion (
  post_id       UUID NOT NULL REFERENCES qb.seo_post (id) ON DELETE CASCADE,
  assertion_id  UUID NOT NULL,
  position      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (post_id, assertion_id)
);
CREATE INDEX IF NOT EXISTS ix_seo_post_assertion_post
  ON qb.seo_post_assertion (post_id, position);

CREATE TABLE IF NOT EXISTS qb.seo_topic_queue (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  topic_key     TEXT NOT NULL UNIQUE,
  stream        TEXT NOT NULL DEFAULT 'system_design',
  title_hint    TEXT NOT NULL,
  angle_prompt  TEXT NOT NULL DEFAULT '',
  priority      INTEGER NOT NULL DEFAULT 100,
  last_used_at  TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT seo_topic_queue_stream_check
    CHECK (stream IN ('general', 'system_design'))
);
CREATE INDEX IF NOT EXISTS ix_seo_topic_queue_pick
  ON qb.seo_topic_queue (stream, priority ASC, last_used_at ASC NULLS FIRST);

CREATE TABLE IF NOT EXISTS qb.seo_cook_attempt (
  source_kind   TEXT NOT NULL,
  source_key    TEXT NOT NULL,
  outcome       TEXT NOT NULL,
  reason        TEXT NOT NULL DEFAULT '',
  post_id       UUID REFERENCES qb.seo_post (id) ON DELETE SET NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (source_kind, source_key),
  CONSTRAINT seo_cook_attempt_outcome_check
    CHECK (outcome IN ('published', 'skipped'))
);

INSERT INTO qb.seo_author (name, active, sort_order) VALUES
  ('Benito JD', true, 10),
  ('Ananya Sharma', true, 20),
  ('Arjun Mehta', true, 30),
  ('Priya Nair', true, 40),
  ('Rohan Iyer', true, 50),
  ('Kavya Reddy', true, 60),
  ('Vikram Singh', true, 70),
  ('Meera Krishnan', true, 80),
  ('Aditya Patel', true, 90),
  ('Sneha Gupta', true, 100)
ON CONFLICT (name) DO NOTHING;

INSERT INTO qb.seo_topic_queue (topic_key, stream, title_hint, angle_prompt, priority) VALUES
  ('caching-basics', 'system_design', 'How caching actually works',
   'Explain caching layers, hit rate, and when cache hurts. One clear idea.', 10),
  ('cap-theorem', 'system_design', 'CAP theorem without the fog',
   'Explain consistency, availability, partition tolerance with one concrete tradeoff.', 20),
  ('rate-limits', 'system_design', 'Rate limiting that protects systems',
   'Token bucket vs sliding window; where to put limits; what users feel.', 30),
  ('message-queues', 'system_design', 'Why systems use message queues',
   'Decoupling, retries, poison messages. Teach the idea, not a vendor.', 40),
  ('load-balancing', 'system_design', 'Load balancing choices that matter',
   'L4 vs L7, sticky sessions, health checks. Short and concrete.', 50),
  ('database-indexing', 'system_design', 'Database indexes that earn their keep',
   'B-tree basics, selectivity, write cost. One mistake learners make.', 60),
  ('idempotency', 'system_design', 'Idempotent APIs under retries',
   'Why retries duplicate work; keys and exactly-once illusions.', 70),
  ('cdn-edge', 'system_design', 'CDNs and the edge',
   'What to cache at the edge, cache keys, and invalidation pain.', 80),
  ('sharding', 'system_design', 'When to shard a database',
   'Hot keys, rebalancing, and why premature sharding hurts.', 90),
  ('observability', 'system_design', 'Logs, metrics, and traces',
   'What each answers; one example of finding a production bug.', 100)
ON CONFLICT (topic_key) DO NOTHING;
