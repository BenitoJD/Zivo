-- =============================================================================
-- INTEL FOUNDATION SCHEMA (A+)
-- =============================================================================
-- Permanent intelligence-store basement for Postgres.
--
-- Fixed for the life of the platform:
--   • Vocabulary in intel.concept (+ metadata / JSON Schema registry)
--   • Immutable evidence in intel.artifact (INSERT-only + fixity audit)
--   • Document segments + embeddings for RAG / semantic retrieval
--   • Append-only identity, coordinates, geometries, status history
--   • Facts in intel.assertion (confidence, supersede / retract, never erase)
--   • Cross-source assertion dedup (match candidates + resolutions)
--   • Quantitative time-series in intel.measurement (rollup-friendly)
--   • Sensitivity, canonical URIs, interchange export registry
--   • Projections disposable; provenance mandatory
--
-- Extensions: pgcrypto, btree_gist, vector (required); postgis (recommended)
--
-- Standards: W3C PROV-DM, OAIS ISO 14721 PDI, EU ePO, GeoJSON RFC 7946
--
-- Apply once:
--   psql "$DATABASE_URL" -f backend/schema/intel_foundation.sql
--
-- Validate:
--   psql "$DATABASE_URL" -f backend/schema/intel_foundation_stress_test.sql
--
-- Operational (not DDL): yearly intel.artifact_YYYY partitions; fixity checks;
-- export PROV-O bundles via interchange_registry.
-- =============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS intel;

CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- PostGIS is optional at apply time; GeoJSON in entity_geometry always works.
DO $postgis$
BEGIN
  CREATE EXTENSION IF NOT EXISTS postgis;
EXCEPTION
  WHEN OTHERS THEN
    RAISE NOTICE 'PostGIS unavailable (%); spatial GiST indexes skipped', SQLERRM;
END;
$postgis$;

-- -----------------------------------------------------------------------------
-- intel.concept
-- Canonical vocabulary + schema registry (metadata.json_schema_uri, etc.).
-- -----------------------------------------------------------------------------
CREATE TABLE intel.concept (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  uri         TEXT NOT NULL,
  family      TEXT NOT NULL,
  -- family: entity_type | assertion_type | relation_type | participant_role
  --         | activity_type | source_domain | projection_type | identifier_scheme
  --         | sensitivity_level | link_type | evidence_role | metric_type
  --         | geometry_type | embedding_model
  label       TEXT NOT NULL,
  parent_id   UUID REFERENCES intel.concept(id),
  metadata    JSONB NOT NULL DEFAULT '{}',
  valid_from  TIMESTAMPTZ NOT NULL DEFAULT now(),
  valid_to    TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT concept_uri_unique UNIQUE (uri),
  CONSTRAINT concept_valid_range CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE INDEX concept_family_idx ON intel.concept (family) WHERE valid_to IS NULL;
CREATE INDEX concept_parent_idx ON intel.concept (parent_id) WHERE valid_to IS NULL;
CREATE INDEX concept_metadata_gin_idx ON intel.concept USING gin (metadata);

-- -----------------------------------------------------------------------------
-- intel.source
-- -----------------------------------------------------------------------------
CREATE TABLE intel.source (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug              TEXT NOT NULL,
  name              TEXT NOT NULL,
  jurisdiction      TEXT,
  domain_concept_id UUID NOT NULL REFERENCES intel.concept(id),
  base_url          TEXT,
  config            JSONB NOT NULL DEFAULT '{}',
  valid_from        TIMESTAMPTZ NOT NULL DEFAULT now(),
  valid_to          TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT source_slug_unique UNIQUE (slug),
  CONSTRAINT source_valid_range CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE INDEX source_domain_idx ON intel.source (domain_concept_id) WHERE valid_to IS NULL;

-- -----------------------------------------------------------------------------
-- intel.source_cursor
-- -----------------------------------------------------------------------------
CREATE TABLE intel.source_cursor (
  source_id     UUID NOT NULL REFERENCES intel.source(id),
  cursor_key    TEXT NOT NULL DEFAULT 'default',
  cursor_value  JSONB NOT NULL DEFAULT '{}',
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

  PRIMARY KEY (source_id, cursor_key)
);

-- -----------------------------------------------------------------------------
-- intel.activity
-- -----------------------------------------------------------------------------
CREATE TABLE intel.activity (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  type_concept_id UUID NOT NULL REFERENCES intel.concept(id),
  source_id       UUID REFERENCES intel.source(id),
  agent           TEXT NOT NULL,
  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at     TIMESTAMPTZ,
  status          TEXT NOT NULL DEFAULT 'running',
  stats           JSONB NOT NULL DEFAULT '{}',
  error_summary   TEXT
);

CREATE INDEX activity_source_started_idx ON intel.activity (source_id, started_at DESC);
CREATE INDEX activity_status_idx ON intel.activity (status) WHERE finished_at IS NULL;

-- -----------------------------------------------------------------------------
-- intel.artifact
-- Immutable evidence (OAIS content + fixity). INSERT-only. Partitioned by time.
-- -----------------------------------------------------------------------------
CREATE TABLE intel.artifact (
  id                     UUID NOT NULL DEFAULT gen_random_uuid(),
  source_id              UUID NOT NULL REFERENCES intel.source(id),
  activity_id            UUID REFERENCES intel.activity(id),
  sensitivity_concept_id UUID REFERENCES intel.concept(id),
  external_key           TEXT NOT NULL,
  checksum_algo          TEXT NOT NULL DEFAULT 'sha256',
  content_hash           TEXT NOT NULL,
  byte_size              BIGINT,
  storage_uri            TEXT,
  payload                JSONB NOT NULL,
  media_type             TEXT NOT NULL DEFAULT 'application/json',
  source_url             TEXT,
  captured_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  fixity_last_verified_at TIMESTAMPTZ,

  PRIMARY KEY (id, captured_at),
  CONSTRAINT artifact_source_key_hash_unique
    UNIQUE (source_id, external_key, content_hash, captured_at),
  CONSTRAINT artifact_byte_size_nonneg CHECK (byte_size IS NULL OR byte_size >= 0)
) PARTITION BY RANGE (captured_at);

CREATE TABLE intel.artifact_default PARTITION OF intel.artifact DEFAULT;

CREATE INDEX artifact_source_key_time_idx
  ON intel.artifact (source_id, external_key, captured_at DESC);
CREATE INDEX artifact_captured_brin_idx
  ON intel.artifact USING brin (captured_at);

-- -----------------------------------------------------------------------------
-- intel.artifact_segment
-- Chunked text/media segments for RAG, citations, and per-segment embeddings.
-- -----------------------------------------------------------------------------
CREATE TABLE intel.artifact_segment (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  artifact_id          UUID NOT NULL,
  artifact_captured_at TIMESTAMPTZ NOT NULL,
  segment_index        INTEGER NOT NULL,
  char_start           INTEGER,
  char_end             INTEGER,
  page_number          INTEGER,
  content_hash         TEXT,
  text_content         TEXT,
  payload              JSONB NOT NULL DEFAULT '{}',
  activity_id          UUID REFERENCES intel.activity(id),
  recorded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT artifact_segment_unique
    UNIQUE (artifact_id, artifact_captured_at, segment_index),
  CONSTRAINT artifact_segment_char_range CHECK (
    (char_start IS NULL AND char_end IS NULL)
    OR (char_start IS NOT NULL AND char_end IS NOT NULL AND char_end >= char_start)
  ),
  CONSTRAINT artifact_segment_page_positive CHECK (page_number IS NULL OR page_number > 0),
  FOREIGN KEY (artifact_id, artifact_captured_at)
    REFERENCES intel.artifact (id, captured_at)
);

CREATE INDEX artifact_segment_artifact_idx
  ON intel.artifact_segment (artifact_id, artifact_captured_at, segment_index);
CREATE INDEX artifact_segment_text_search_idx ON intel.artifact_segment USING gin (
  to_tsvector('simple', coalesce(text_content, ''))
);

-- -----------------------------------------------------------------------------
-- intel.fixity_check
-- -----------------------------------------------------------------------------
CREATE TABLE intel.fixity_check (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  artifact_id          UUID NOT NULL,
  artifact_captured_at TIMESTAMPTZ NOT NULL,
  expected_hash        TEXT NOT NULL,
  observed_hash        TEXT NOT NULL,
  outcome              TEXT NOT NULL,
  activity_id          UUID REFERENCES intel.activity(id),
  checked_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fixity_check_outcome_valid CHECK (outcome IN ('match', 'mismatch', 'unavailable')),
  FOREIGN KEY (artifact_id, artifact_captured_at)
    REFERENCES intel.artifact (id, captured_at)
);

CREATE INDEX fixity_check_artifact_idx
  ON intel.fixity_check (artifact_id, artifact_captured_at, checked_at DESC);

-- -----------------------------------------------------------------------------
-- intel.redaction_notice
-- -----------------------------------------------------------------------------
CREATE TABLE intel.redaction_notice (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  artifact_id             UUID NOT NULL,
  artifact_captured_at    TIMESTAMPTZ NOT NULL,
  reason                  TEXT NOT NULL,
  legal_basis             TEXT,
  replacement_storage_uri TEXT,
  activity_id             UUID REFERENCES intel.activity(id),
  redacted_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

  FOREIGN KEY (artifact_id, artifact_captured_at)
    REFERENCES intel.artifact (id, captured_at)
);

CREATE INDEX redaction_notice_artifact_idx
  ON intel.redaction_notice (artifact_id, artifact_captured_at);

-- -----------------------------------------------------------------------------
-- intel.entity
-- -----------------------------------------------------------------------------
CREATE TABLE intel.entity (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  type_concept_id        UUID NOT NULL REFERENCES intel.concept(id),
  sensitivity_concept_id UUID REFERENCES intel.concept(id),
  canonical_uri          TEXT NOT NULL,
  status                 TEXT NOT NULL DEFAULT 'active',
  merged_into_id         UUID REFERENCES intel.entity(id),
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT entity_canonical_uri_unique UNIQUE (canonical_uri),
  CONSTRAINT entity_merged_consistency CHECK (
    (status = 'merged' AND merged_into_id IS NOT NULL)
    OR (status <> 'merged' AND merged_into_id IS NULL)
  )
);

CREATE INDEX entity_type_idx ON intel.entity (type_concept_id);
CREATE INDEX entity_merged_into_idx ON intel.entity (merged_into_id) WHERE merged_into_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- intel.entity_identifier
-- -----------------------------------------------------------------------------
CREATE TABLE intel.entity_identifier (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id   UUID NOT NULL REFERENCES intel.entity(id),
  scheme      TEXT NOT NULL,
  value       TEXT NOT NULL,
  jurisdiction TEXT,
  source_id   UUID REFERENCES intel.source(id),
  activity_id UUID REFERENCES intel.activity(id),
  valid_from  TIMESTAMPTZ NOT NULL,
  valid_to    TIMESTAMPTZ,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  replaces_id UUID REFERENCES intel.entity_identifier(id),

  CONSTRAINT entity_identifier_valid_range CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE INDEX entity_identifier_entity_idx ON intel.entity_identifier (entity_id, valid_from DESC);
CREATE INDEX entity_identifier_lookup_idx ON intel.entity_identifier (scheme, value, jurisdiction)
  WHERE valid_to IS NULL;

CREATE UNIQUE INDEX entity_identifier_active_unique_idx
  ON intel.entity_identifier (scheme, value, COALESCE(jurisdiction, ''))
  WHERE valid_to IS NULL;

-- -----------------------------------------------------------------------------
-- intel.entity_label
-- -----------------------------------------------------------------------------
CREATE TABLE intel.entity_label (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id       UUID NOT NULL REFERENCES intel.entity(id),
  label           TEXT NOT NULL,
  label_normalized TEXT NOT NULL,
  label_concept_id UUID REFERENCES intel.concept(id),
  language        TEXT,
  source_id       UUID REFERENCES intel.source(id),
  activity_id     UUID REFERENCES intel.activity(id),
  confidence      NUMERIC(5, 4) NOT NULL DEFAULT 1.0000,
  valid_from      TIMESTAMPTZ NOT NULL,
  valid_to        TIMESTAMPTZ,
  recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  replaces_id     UUID REFERENCES intel.entity_label(id),

  CONSTRAINT entity_label_valid_range CHECK (valid_to IS NULL OR valid_to > valid_from),
  CONSTRAINT entity_label_confidence_range CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX entity_label_entity_idx ON intel.entity_label (entity_id, valid_from DESC);
CREATE INDEX entity_label_normalized_idx ON intel.entity_label (label_normalized)
  WHERE valid_to IS NULL;
CREATE INDEX entity_label_search_idx ON intel.entity_label USING gin (
  to_tsvector('simple', label_normalized)
);

-- -----------------------------------------------------------------------------
-- intel.entity_coordinate
-- Point locations (HQ, vessel ping, facility). For polygons/routes use entity_geometry.
-- -----------------------------------------------------------------------------
CREATE TABLE intel.entity_coordinate (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id        UUID NOT NULL REFERENCES intel.entity(id),
  longitude        DOUBLE PRECISION NOT NULL,
  latitude         DOUBLE PRECISION NOT NULL,
  precision_meters NUMERIC(12, 2),
  source_id        UUID REFERENCES intel.source(id),
  activity_id      UUID REFERENCES intel.activity(id),
  valid_from       TIMESTAMPTZ NOT NULL,
  valid_to         TIMESTAMPTZ,
  recorded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  replaces_id      UUID REFERENCES intel.entity_coordinate(id),

  CONSTRAINT entity_coordinate_valid_range CHECK (valid_to IS NULL OR valid_to > valid_from),
  CONSTRAINT entity_coordinate_lon_range CHECK (longitude >= -180 AND longitude <= 180),
  CONSTRAINT entity_coordinate_lat_range CHECK (latitude >= -90 AND latitude <= 90)
);

CREATE INDEX entity_coordinate_entity_idx
  ON intel.entity_coordinate (entity_id, valid_from DESC);
CREATE INDEX entity_coordinate_gist_idx
  ON intel.entity_coordinate USING gist (
    point(longitude, latitude)
  ) WHERE valid_to IS NULL;

-- -----------------------------------------------------------------------------
-- intel.entity_geometry
-- GeoJSON geometries: country borders, shipping lanes, facility footprints.
-- geom column + GiST added when PostGIS is present (see end of file).
-- -----------------------------------------------------------------------------
CREATE TABLE intel.entity_geometry (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id           UUID NOT NULL REFERENCES intel.entity(id),
  geometry_concept_id UUID NOT NULL REFERENCES intel.concept(id),
  geojson             JSONB NOT NULL,
  srid                INTEGER NOT NULL DEFAULT 4326,
  precision_meters    NUMERIC(12, 2),
  source_id           UUID REFERENCES intel.source(id),
  activity_id         UUID REFERENCES intel.activity(id),
  valid_from          TIMESTAMPTZ NOT NULL,
  valid_to            TIMESTAMPTZ,
  recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  replaces_id         UUID REFERENCES intel.entity_geometry(id),

  CONSTRAINT entity_geometry_valid_range CHECK (valid_to IS NULL OR valid_to > valid_from),
  CONSTRAINT entity_geometry_geojson_object CHECK (
    jsonb_typeof(geojson) = 'object'
    AND geojson ? 'type'
    AND geojson ? 'coordinates'
  ),
  CONSTRAINT entity_geometry_srid_wgs84 CHECK (srid = 4326)
);

CREATE INDEX entity_geometry_entity_idx
  ON intel.entity_geometry (entity_id, valid_from DESC);
CREATE INDEX entity_geometry_geojson_gin_idx
  ON intel.entity_geometry USING gin (geojson);

-- -----------------------------------------------------------------------------
-- intel.entity_match_candidate
-- -----------------------------------------------------------------------------
CREATE TABLE intel.entity_match_candidate (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_a_id   UUID NOT NULL REFERENCES intel.entity(id),
  entity_b_id   UUID NOT NULL REFERENCES intel.entity(id),
  score         NUMERIC(5, 4) NOT NULL,
  method        TEXT NOT NULL,
  evidence      JSONB NOT NULL DEFAULT '{}',
  status        TEXT NOT NULL DEFAULT 'pending',
  activity_id   UUID REFERENCES intel.activity(id),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at   TIMESTAMPTZ,
  resolution_id UUID,

  CONSTRAINT entity_match_candidate_pair_order CHECK (entity_a_id < entity_b_id),
  CONSTRAINT entity_match_candidate_distinct CHECK (entity_a_id <> entity_b_id),
  CONSTRAINT entity_match_candidate_score_range CHECK (score >= 0 AND score <= 1),
  CONSTRAINT entity_match_candidate_status_valid CHECK (
    status IN ('pending', 'accepted', 'rejected', 'deferred')
  )
);

CREATE INDEX entity_match_candidate_pending_idx
  ON intel.entity_match_candidate (status, score DESC) WHERE status = 'pending';

-- -----------------------------------------------------------------------------
-- intel.entity_resolution
-- -----------------------------------------------------------------------------
CREATE TABLE intel.entity_resolution (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_a_id      UUID NOT NULL REFERENCES intel.entity(id),
  entity_b_id      UUID NOT NULL REFERENCES intel.entity(id),
  outcome          TEXT NOT NULL,
  method           TEXT NOT NULL,
  confidence       NUMERIC(5, 4) NOT NULL,
  activity_id      UUID REFERENCES intel.activity(id),
  match_candidate_id UUID REFERENCES intel.entity_match_candidate(id),
  decided_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  evidence         JSONB NOT NULL DEFAULT '{}',

  CONSTRAINT entity_resolution_pair_order CHECK (entity_a_id < entity_b_id),
  CONSTRAINT entity_resolution_pair_unique UNIQUE (entity_a_id, entity_b_id),
  CONSTRAINT entity_resolution_distinct_pair CHECK (entity_a_id <> entity_b_id),
  CONSTRAINT entity_resolution_confidence_range CHECK (confidence >= 0 AND confidence <= 1),
  CONSTRAINT entity_resolution_outcome_valid CHECK (
    outcome IN ('same', 'distinct', 'uncertain')
  )
);

-- -----------------------------------------------------------------------------
-- intel.relation
-- -----------------------------------------------------------------------------
CREATE TABLE intel.relation (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  from_entity_id     UUID NOT NULL REFERENCES intel.entity(id),
  to_entity_id       UUID NOT NULL REFERENCES intel.entity(id),
  type_concept_id    UUID NOT NULL REFERENCES intel.concept(id),
  valid_from         TIMESTAMPTZ NOT NULL,
  valid_to           TIMESTAMPTZ,
  recorded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  source_assertion_id UUID,
  activity_id        UUID REFERENCES intel.activity(id),
  confidence         NUMERIC(5, 4) NOT NULL DEFAULT 1.0000,
  meta               JSONB NOT NULL DEFAULT '{}',
  replaces_id        UUID REFERENCES intel.relation(id),

  CONSTRAINT relation_valid_range CHECK (valid_to IS NULL OR valid_to > valid_from),
  CONSTRAINT relation_distinct_entities CHECK (from_entity_id <> to_entity_id),
  CONSTRAINT relation_confidence_range CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX relation_from_idx ON intel.relation (from_entity_id, type_concept_id) WHERE valid_to IS NULL;
CREATE INDEX relation_to_idx ON intel.relation (to_entity_id, type_concept_id) WHERE valid_to IS NULL;

-- -----------------------------------------------------------------------------
-- intel.assertion
-- -----------------------------------------------------------------------------
CREATE TABLE intel.assertion (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  type_concept_id        UUID NOT NULL REFERENCES intel.concept(id),
  source_id              UUID NOT NULL REFERENCES intel.source(id),
  activity_id            UUID REFERENCES intel.activity(id),
  sensitivity_concept_id UUID REFERENCES intel.concept(id),
  canonical_uri          TEXT NOT NULL,
  fingerprint            TEXT NOT NULL,
  confidence             NUMERIC(5, 4) NOT NULL DEFAULT 1.0000,

  valid_from             TIMESTAMPTZ,
  valid_to               TIMESTAMPTZ,
  recorded_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

  title                  TEXT,
  summary                TEXT,
  payload                JSONB NOT NULL DEFAULT '{}',
  payload_version        INTEGER NOT NULL DEFAULT 1,

  status                 TEXT NOT NULL DEFAULT 'active',
  superseded_by          UUID REFERENCES intel.assertion(id),
  retracted_at           TIMESTAMPTZ,
  retraction_reason      TEXT,

  search_document TSVECTOR GENERATED ALWAYS AS (
    to_tsvector(
      'simple',
      coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(payload::text, '')
    )
  ) STORED,

  CONSTRAINT assertion_canonical_uri_unique UNIQUE (canonical_uri),
  CONSTRAINT assertion_source_fingerprint_unique UNIQUE (source_id, fingerprint),
  CONSTRAINT assertion_confidence_range CHECK (confidence >= 0 AND confidence <= 1),
  CONSTRAINT assertion_valid_range CHECK (
    (valid_from IS NULL AND valid_to IS NULL)
    OR (valid_from IS NOT NULL AND (valid_to IS NULL OR valid_to > valid_from))
  ),
  CONSTRAINT assertion_supersede_consistency CHECK (
    (status = 'superseded' AND superseded_by IS NOT NULL)
    OR (status <> 'superseded')
  ),
  CONSTRAINT assertion_retract_consistency CHECK (
    (status = 'retracted' AND retracted_at IS NOT NULL)
    OR (status <> 'retracted')
  ),
  CONSTRAINT assertion_status_valid CHECK (
    status IN ('active', 'superseded', 'retracted')
  )
);

CREATE INDEX assertion_type_valid_idx ON intel.assertion (type_concept_id, valid_from DESC)
  WHERE status = 'active';
CREATE INDEX assertion_confidence_idx ON intel.assertion (confidence DESC)
  WHERE status = 'active';
CREATE INDEX assertion_recorded_idx ON intel.assertion (recorded_at DESC);
CREATE INDEX assertion_recorded_brin_idx ON intel.assertion USING brin (recorded_at);
CREATE INDEX assertion_payload_gin_idx ON intel.assertion USING gin (payload);
CREATE INDEX assertion_search_gin_idx ON intel.assertion USING gin (search_document);

-- -----------------------------------------------------------------------------
-- intel.assertion_status_log
-- -----------------------------------------------------------------------------
CREATE TABLE intel.assertion_status_log (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  assertion_id  UUID NOT NULL REFERENCES intel.assertion(id),
  from_status   TEXT NOT NULL,
  to_status     TEXT NOT NULL,
  reason        TEXT,
  superseded_by UUID REFERENCES intel.assertion(id),
  activity_id   UUID REFERENCES intel.activity(id),
  changed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX assertion_status_log_assertion_idx
  ON intel.assertion_status_log (assertion_id, changed_at DESC);

-- -----------------------------------------------------------------------------
-- intel.assertion_participant
-- -----------------------------------------------------------------------------
CREATE TABLE intel.assertion_participant (
  assertion_id    UUID NOT NULL REFERENCES intel.assertion(id),
  entity_id       UUID NOT NULL REFERENCES intel.entity(id),
  role_concept_id UUID NOT NULL REFERENCES intel.concept(id),
  confidence      NUMERIC(5, 4) NOT NULL DEFAULT 1.0000,
  meta            JSONB NOT NULL DEFAULT '{}',

  PRIMARY KEY (assertion_id, entity_id, role_concept_id),
  CONSTRAINT assertion_participant_confidence_range CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX assertion_participant_entity_idx
  ON intel.assertion_participant (entity_id, role_concept_id);

-- -----------------------------------------------------------------------------
-- intel.assertion_evidence
-- -----------------------------------------------------------------------------
CREATE TABLE intel.assertion_evidence (
  assertion_id         UUID NOT NULL REFERENCES intel.assertion(id),
  artifact_id          UUID NOT NULL,
  artifact_captured_at TIMESTAMPTZ NOT NULL,
  role_concept_id      UUID NOT NULL REFERENCES intel.concept(id),
  confidence           NUMERIC(5, 4) NOT NULL DEFAULT 1.0000,
  meta                 JSONB NOT NULL DEFAULT '{}',

  PRIMARY KEY (assertion_id, artifact_id, artifact_captured_at),
  CONSTRAINT assertion_evidence_confidence_range CHECK (confidence >= 0 AND confidence <= 1),
  FOREIGN KEY (artifact_id, artifact_captured_at)
    REFERENCES intel.artifact (id, captured_at)
);

-- -----------------------------------------------------------------------------
-- intel.assertion_lineage
-- Link types are vocabulary rows (family = link_type), not hard-coded enums.
-- -----------------------------------------------------------------------------
CREATE TABLE intel.assertion_lineage (
  from_assertion_id      UUID NOT NULL REFERENCES intel.assertion(id),
  to_assertion_id        UUID NOT NULL REFERENCES intel.assertion(id),
  link_type_concept_id   UUID NOT NULL REFERENCES intel.concept(id),
  confidence             NUMERIC(5, 4) NOT NULL DEFAULT 1.0000,
  activity_id            UUID REFERENCES intel.activity(id),
  recorded_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

  PRIMARY KEY (from_assertion_id, to_assertion_id, link_type_concept_id),
  CONSTRAINT assertion_lineage_distinct CHECK (from_assertion_id <> to_assertion_id),
  CONSTRAINT assertion_lineage_confidence_range CHECK (confidence >= 0 AND confidence <= 1)
);

-- -----------------------------------------------------------------------------
-- intel.assertion_match_candidate
-- Cross-source / cross-capture dedup queue for assertions (e.g. same job on 2 boards).
-- -----------------------------------------------------------------------------
CREATE TABLE intel.assertion_match_candidate (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  assertion_a_id UUID NOT NULL REFERENCES intel.assertion(id),
  assertion_b_id UUID NOT NULL REFERENCES intel.assertion(id),
  score         NUMERIC(5, 4) NOT NULL,
  method        TEXT NOT NULL,
  evidence      JSONB NOT NULL DEFAULT '{}',
  status        TEXT NOT NULL DEFAULT 'pending',
  activity_id   UUID REFERENCES intel.activity(id),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at   TIMESTAMPTZ,
  resolution_id UUID,

  CONSTRAINT assertion_match_candidate_pair_order CHECK (assertion_a_id < assertion_b_id),
  CONSTRAINT assertion_match_candidate_distinct CHECK (assertion_a_id <> assertion_b_id),
  CONSTRAINT assertion_match_candidate_score_range CHECK (score >= 0 AND score <= 1),
  CONSTRAINT assertion_match_candidate_status_valid CHECK (
    status IN ('pending', 'accepted', 'rejected', 'deferred')
  )
);

CREATE INDEX assertion_match_candidate_pending_idx
  ON intel.assertion_match_candidate (status, score DESC) WHERE status = 'pending';

-- -----------------------------------------------------------------------------
-- intel.assertion_resolution
-- -----------------------------------------------------------------------------
CREATE TABLE intel.assertion_resolution (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  assertion_a_id   UUID NOT NULL REFERENCES intel.assertion(id),
  assertion_b_id   UUID NOT NULL REFERENCES intel.assertion(id),
  outcome          TEXT NOT NULL,
  method           TEXT NOT NULL,
  confidence       NUMERIC(5, 4) NOT NULL,
  activity_id      UUID REFERENCES intel.activity(id),
  match_candidate_id UUID REFERENCES intel.assertion_match_candidate(id),
  decided_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  evidence         JSONB NOT NULL DEFAULT '{}',

  CONSTRAINT assertion_resolution_pair_order CHECK (assertion_a_id < assertion_b_id),
  CONSTRAINT assertion_resolution_pair_unique UNIQUE (assertion_a_id, assertion_b_id),
  CONSTRAINT assertion_resolution_distinct_pair CHECK (assertion_a_id <> assertion_b_id),
  CONSTRAINT assertion_resolution_confidence_range CHECK (confidence >= 0 AND confidence <= 1),
  CONSTRAINT assertion_resolution_outcome_valid CHECK (
    outcome IN ('same', 'distinct', 'uncertain', 'related')
  )
);

-- -----------------------------------------------------------------------------
-- intel.projection
-- -----------------------------------------------------------------------------
CREATE TABLE intel.projection (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  type_concept_id     UUID NOT NULL REFERENCES intel.concept(id),
  subject_entity_id   UUID REFERENCES intel.entity(id),
  subject_assertion_id UUID REFERENCES intel.assertion(id),
  as_of               TIMESTAMPTZ NOT NULL,
  value               JSONB NOT NULL,
  built_through       TIMESTAMPTZ NOT NULL,
  activity_id         UUID REFERENCES intel.activity(id),
  computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT projection_subject_present CHECK (
    subject_entity_id IS NOT NULL OR subject_assertion_id IS NOT NULL
  )
);

CREATE INDEX projection_type_subject_idx
  ON intel.projection (type_concept_id, subject_entity_id, as_of DESC);
CREATE INDEX projection_type_assertion_idx
  ON intel.projection (type_concept_id, subject_assertion_id, as_of DESC);

-- -----------------------------------------------------------------------------
-- intel.measurement
-- High-frequency quantitative observations (prices, weather, AIS pings).
-- Roll up to assertions/projections; keep raw series here with provenance links.
-- -----------------------------------------------------------------------------
CREATE TABLE intel.measurement (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  subject_entity_id    UUID REFERENCES intel.entity(id),
  metric_concept_id    UUID NOT NULL REFERENCES intel.concept(id),
  observed_at          TIMESTAMPTZ NOT NULL,
  value_numeric        NUMERIC,
  value_text           TEXT,
  value_json           JSONB,
  unit                 TEXT,
  source_assertion_id  UUID REFERENCES intel.assertion(id),
  artifact_id          UUID,
  artifact_captured_at TIMESTAMPTZ,
  activity_id          UUID REFERENCES intel.activity(id),
  confidence           NUMERIC(5, 4) NOT NULL DEFAULT 1.0000,
  recorded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT measurement_confidence_range CHECK (confidence >= 0 AND confidence <= 1),
  CONSTRAINT measurement_value_present CHECK (
    value_numeric IS NOT NULL OR value_text IS NOT NULL OR value_json IS NOT NULL
  ),
  CONSTRAINT measurement_subject_present CHECK (
    subject_entity_id IS NOT NULL OR source_assertion_id IS NOT NULL
  ),
  FOREIGN KEY (artifact_id, artifact_captured_at)
    REFERENCES intel.artifact (id, captured_at)
);

CREATE INDEX measurement_entity_metric_time_idx
  ON intel.measurement (subject_entity_id, metric_concept_id, observed_at DESC);
CREATE INDEX measurement_observed_brin_idx
  ON intel.measurement USING brin (observed_at);
CREATE INDEX measurement_assertion_idx
  ON intel.measurement (source_assertion_id) WHERE source_assertion_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- intel.embedding
-- Semantic vectors for entities, assertions, and artifact segments.
-- -----------------------------------------------------------------------------
CREATE TABLE intel.embedding (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_kind   TEXT NOT NULL,
  resource_id     UUID NOT NULL,
  model_concept_id UUID NOT NULL REFERENCES intel.concept(id),
  dimensions      INTEGER NOT NULL,
  embedding       vector NOT NULL,
  content_hash    TEXT,
  activity_id     UUID REFERENCES intel.activity(id),
  recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT embedding_resource_kind_valid CHECK (
    resource_kind IN ('entity', 'assertion', 'artifact_segment')
  ),
  CONSTRAINT embedding_dimensions_positive CHECK (dimensions > 0),
  CONSTRAINT embedding_resource_model_unique UNIQUE (resource_kind, resource_id, model_concept_id)
);

CREATE INDEX embedding_model_idx ON intel.embedding (model_concept_id, resource_kind);

-- -----------------------------------------------------------------------------
-- intel.interchange_registry
-- -----------------------------------------------------------------------------
CREATE TABLE intel.interchange_registry (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_kind TEXT NOT NULL,
  resource_id   UUID NOT NULL,
  format        TEXT NOT NULL,
  external_uri  TEXT NOT NULL,
  content_hash  TEXT,
  activity_id   UUID REFERENCES intel.activity(id),
  exported_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT interchange_resource_kind_valid CHECK (
    resource_kind IN (
      'entity', 'assertion', 'artifact', 'artifact_segment',
      'measurement', 'embedding', 'bundle'
    )
  ),
  CONSTRAINT interchange_format_valid CHECK (
    format IN ('prov-o', 'rdf', 'json-ld', 'turtle', 'n-quads', 'geojson')
  ),
  CONSTRAINT interchange_external_uri_unique UNIQUE (format, external_uri)
);

CREATE INDEX interchange_registry_resource_idx
  ON intel.interchange_registry (resource_kind, resource_id, exported_at DESC);

-- -----------------------------------------------------------------------------
-- Deferred FKs
-- -----------------------------------------------------------------------------
ALTER TABLE intel.relation
  ADD CONSTRAINT relation_source_assertion_fk
  FOREIGN KEY (source_assertion_id) REFERENCES intel.assertion(id);

ALTER TABLE intel.entity_match_candidate
  ADD CONSTRAINT entity_match_candidate_resolution_fk
  FOREIGN KEY (resolution_id) REFERENCES intel.entity_resolution(id);

ALTER TABLE intel.assertion_match_candidate
  ADD CONSTRAINT assertion_match_candidate_resolution_fk
  FOREIGN KEY (resolution_id) REFERENCES intel.assertion_resolution(id);

-- -----------------------------------------------------------------------------
-- PostGIS geom column (when extension is available)
-- -----------------------------------------------------------------------------
DO $geom$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'postgis') THEN
    EXECUTE $sql$
      ALTER TABLE intel.entity_geometry
        ADD COLUMN IF NOT EXISTS geom geometry(Geometry, 4326)
    $sql$;

    EXECUTE $sql$
      CREATE OR REPLACE FUNCTION intel.sync_entity_geometry_geom()
      RETURNS trigger
      LANGUAGE plpgsql
      AS $fn$
      BEGIN
        IF NEW.geom IS NULL AND NEW.geojson IS NOT NULL THEN
          NEW.geom := ST_SetSRID(ST_GeomFromGeoJSON(NEW.geojson::text), 4326);
        END IF;
        RETURN NEW;
      END;
      $fn$
    $sql$;

    EXECUTE $sql$
      DROP TRIGGER IF EXISTS entity_geometry_sync_geom ON intel.entity_geometry
    $sql$;

    EXECUTE $sql$
      CREATE TRIGGER entity_geometry_sync_geom
        BEFORE INSERT OR UPDATE OF geojson, geom ON intel.entity_geometry
        FOR EACH ROW EXECUTE FUNCTION intel.sync_entity_geometry_geom()
    $sql$;

    EXECUTE $sql$
      CREATE INDEX IF NOT EXISTS entity_geometry_geom_gist_idx
        ON intel.entity_geometry USING gist (geom)
        WHERE valid_to IS NULL
    $sql$;
  END IF;
END;
$geom$;

-- -----------------------------------------------------------------------------
-- Immutability guards
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION intel.reject_update_delete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'intel.% is immutable (INSERT only)', TG_TABLE_NAME;
END;
$$;

CREATE TRIGGER artifact_immutable
  BEFORE UPDATE OR DELETE ON intel.artifact
  FOR EACH ROW EXECUTE FUNCTION intel.reject_update_delete();

CREATE TRIGGER artifact_segment_immutable
  BEFORE UPDATE OR DELETE ON intel.artifact_segment
  FOR EACH ROW EXECUTE FUNCTION intel.reject_update_delete();

CREATE TRIGGER fixity_check_immutable
  BEFORE UPDATE OR DELETE ON intel.fixity_check
  FOR EACH ROW EXECUTE FUNCTION intel.reject_update_delete();

CREATE TRIGGER redaction_notice_immutable
  BEFORE UPDATE OR DELETE ON intel.redaction_notice
  FOR EACH ROW EXECUTE FUNCTION intel.reject_update_delete();

CREATE TRIGGER assertion_status_log_immutable
  BEFORE UPDATE OR DELETE ON intel.assertion_status_log
  FOR EACH ROW EXECUTE FUNCTION intel.reject_update_delete();

CREATE TRIGGER interchange_registry_immutable
  BEFORE UPDATE OR DELETE ON intel.interchange_registry
  FOR EACH ROW EXECUTE FUNCTION intel.reject_update_delete();

CREATE TRIGGER measurement_immutable
  BEFORE UPDATE OR DELETE ON intel.measurement
  FOR EACH ROW EXECUTE FUNCTION intel.reject_update_delete();

CREATE TRIGGER embedding_immutable
  BEFORE UPDATE OR DELETE ON intel.embedding
  FOR EACH ROW EXECUTE FUNCTION intel.reject_update_delete();

-- -----------------------------------------------------------------------------
-- Assertion status audit trigger
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION intel.log_assertion_status_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.status IS DISTINCT FROM NEW.status THEN
    INSERT INTO intel.assertion_status_log (
      assertion_id, from_status, to_status, reason, superseded_by, activity_id
    ) VALUES (
      NEW.id,
      OLD.status,
      NEW.status,
      NEW.retraction_reason,
      NEW.superseded_by,
      NEW.activity_id
    );
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER assertion_status_audit
  AFTER UPDATE OF status, superseded_by, retraction_reason ON intel.assertion
  FOR EACH ROW EXECUTE FUNCTION intel.log_assertion_status_change();

-- -----------------------------------------------------------------------------
-- Seed vocabulary
-- -----------------------------------------------------------------------------
INSERT INTO intel.concept (uri, family, label, metadata) VALUES
  ('/vocab/family/entity_type', 'meta', 'Entity type family', '{}'),
  ('/vocab/family/assertion_type', 'meta', 'Assertion type family', '{}'),
  ('/vocab/family/relation_type', 'meta', 'Relation type family', '{}'),
  ('/vocab/family/participant_role', 'meta', 'Participant role family', '{}'),
  ('/vocab/family/activity_type', 'meta', 'Activity type family', '{}'),
  ('/vocab/family/source_domain', 'meta', 'Source domain family', '{}'),
  ('/vocab/family/projection_type', 'meta', 'Projection type family', '{}'),
  ('/vocab/family/identifier_scheme', 'meta', 'Identifier scheme family', '{}'),
  ('/vocab/family/sensitivity_level', 'meta', 'Sensitivity level family', '{}'),
  ('/vocab/family/link_type', 'meta', 'Assertion link type family', '{}'),
  ('/vocab/family/evidence_role', 'meta', 'Evidence role family', '{}'),
  ('/vocab/family/metric_type', 'meta', 'Measurement metric family', '{}'),
  ('/vocab/family/geometry_type', 'meta', 'Geometry type family', '{}'),
  ('/vocab/family/embedding_model', 'meta', 'Embedding model family', '{}'),

  ('/vocab/entity/organization', 'entity_type', 'Organization', '{}'),
  ('/vocab/entity/person', 'entity_type', 'Person', '{}'),
  ('/vocab/entity/place', 'entity_type', 'Place', '{}'),
  ('/vocab/entity/category', 'entity_type', 'Category', '{}'),
  ('/vocab/entity/instrument', 'entity_type', 'Instrument', '{}'),
  ('/vocab/entity/event', 'entity_type', 'Event', '{}'),
  ('/vocab/entity/document', 'entity_type', 'Document', '{}'),
  ('/vocab/entity/product', 'entity_type', 'Product', '{}'),

  ('/vocab/assertion/tender.published', 'assertion_type', 'Tender published', '{}'),
  ('/vocab/assertion/contract.awarded', 'assertion_type', 'Contract awarded', '{}'),
  ('/vocab/assertion/org.incorporated', 'assertion_type', 'Organization incorporated', '{}'),
  ('/vocab/assertion/org.status_changed', 'assertion_type', 'Organization status changed', '{}'),
  ('/vocab/assertion/person.appointed', 'assertion_type', 'Person appointed to role', '{}'),
  ('/vocab/assertion/job.posted', 'assertion_type', 'Job posted', '{}'),
  ('/vocab/assertion/sanction.listed', 'assertion_type', 'Sanction listing', '{}'),
  ('/vocab/assertion/article.published', 'assertion_type', 'Article published', '{}'),
  ('/vocab/assertion/vessel.position_observed', 'assertion_type', 'Vessel position observed', '{}'),
  ('/vocab/assertion/weather.observed', 'assertion_type', 'Weather observed', '{}'),
  ('/vocab/assertion/patent.granted', 'assertion_type', 'Patent granted', '{}'),
  ('/vocab/assertion/case.filed', 'assertion_type', 'Legal case filed', '{}'),
  ('/vocab/assertion/transfer.observed', 'assertion_type', 'On-chain transfer observed', '{}'),

  ('/vocab/role/subject', 'participant_role', 'Subject', '{}'),
  ('/vocab/role/buyer', 'participant_role', 'Buyer', '{}'),
  ('/vocab/role/winner', 'participant_role', 'Winner', '{}'),
  ('/vocab/role/bidder', 'participant_role', 'Bidder', '{}'),
  ('/vocab/role/director', 'participant_role', 'Director', '{}'),
  ('/vocab/role/owner', 'participant_role', 'Owner', '{}'),
  ('/vocab/role/issuer', 'participant_role', 'Issuer', '{}'),
  ('/vocab/role/counterparty', 'participant_role', 'Counterparty', '{}'),
  ('/vocab/role/mentioned', 'participant_role', 'Mentioned', '{}'),
  ('/vocab/role/employer', 'participant_role', 'Employer', '{}'),
  ('/vocab/role/recruiter', 'participant_role', 'Recruiter', '{}'),
  ('/vocab/role/plaintiff', 'participant_role', 'Plaintiff', '{}'),
  ('/vocab/role/defendant', 'participant_role', 'Defendant', '{}'),
  ('/vocab/role/inventor', 'participant_role', 'Inventor', '{}'),
  ('/vocab/role/victim', 'participant_role', 'Victim', '{}'),
  ('/vocab/role/perpetrator', 'participant_role', 'Perpetrator', '{}'),

  ('/vocab/relation/director_of', 'relation_type', 'Director of', '{}'),
  ('/vocab/relation/owner_of', 'relation_type', 'Owner of', '{}'),
  ('/vocab/relation/subsidiary_of', 'relation_type', 'Subsidiary of', '{}'),
  ('/vocab/relation/located_in', 'relation_type', 'Located in', '{}'),
  ('/vocab/relation/operates_in', 'relation_type', 'Operates in', '{}'),
  ('/vocab/relation/bid_with', 'relation_type', 'Bid with', '{}'),
  ('/vocab/relation/cited_by', 'relation_type', 'Cited by', '{}'),
  ('/vocab/relation/allied_with', 'relation_type', 'Allied with', '{}'),

  ('/vocab/activity/ingest', 'activity_type', 'Ingest', '{}'),
  ('/vocab/activity/normalize', 'activity_type', 'Normalize', '{}'),
  ('/vocab/activity/resolve', 'activity_type', 'Resolve entities', '{}'),
  ('/vocab/activity/project', 'activity_type', 'Build projection', '{}'),
  ('/vocab/activity/retract', 'activity_type', 'Retract assertion', '{}'),
  ('/vocab/activity/fixity', 'activity_type', 'Verify fixity', '{}'),
  ('/vocab/activity/export', 'activity_type', 'Export interchange bundle', '{}'),
  ('/vocab/activity/embed', 'activity_type', 'Compute embeddings', '{}'),

  ('/vocab/domain/procurement', 'source_domain', 'Public procurement', '{}'),
  ('/vocab/domain/registry', 'source_domain', 'Corporate registry', '{}'),
  ('/vocab/domain/legal', 'source_domain', 'Legal records', '{}'),
  ('/vocab/domain/financial', 'source_domain', 'Financial disclosures', '{}'),
  ('/vocab/domain/media', 'source_domain', 'Public media', '{}'),
  ('/vocab/domain/job_board', 'source_domain', 'Job board', '{}'),
  ('/vocab/domain/maritime', 'source_domain', 'Maritime tracking', '{}'),
  ('/vocab/domain/geopolitical', 'source_domain', 'Geopolitical intelligence', '{}'),
  ('/vocab/domain/scientific', 'source_domain', 'Scientific literature', '{}'),
  ('/vocab/domain/weather', 'source_domain', 'Weather observations', '{}'),
  ('/vocab/domain/blockchain', 'source_domain', 'Public blockchain', '{}'),

  ('/vocab/id/cin', 'identifier_scheme', 'India CIN', '{}'),
  ('/vocab/id/lei', 'identifier_scheme', 'Legal Entity Identifier', '{}'),
  ('/vocab/id/uk_company_number', 'identifier_scheme', 'UK company number', '{}'),
  ('/vocab/id/gstin', 'identifier_scheme', 'India GSTIN', '{}'),
  ('/vocab/id/gem_seller', 'identifier_scheme', 'GeM seller ID', '{}'),
  ('/vocab/id/imo', 'identifier_scheme', 'IMO ship number', '{}'),
  ('/vocab/id/mmsi', 'identifier_scheme', 'Maritime MMSI', '{}'),
  ('/vocab/id/patent_number', 'identifier_scheme', 'Patent number', '{}'),
  ('/vocab/id/docket_number', 'identifier_scheme', 'Court docket number', '{}'),

  ('/vocab/sensitivity/public', 'sensitivity_level', 'Public', '{}'),
  ('/vocab/sensitivity/internal', 'sensitivity_level', 'Internal', '{}'),
  ('/vocab/sensitivity/confidential', 'sensitivity_level', 'Confidential', '{}'),
  ('/vocab/sensitivity/restricted', 'sensitivity_level', 'Restricted', '{}'),

  ('/vocab/link/supersedes', 'link_type', 'Supersedes', '{}'),
  ('/vocab/link/derives', 'link_type', 'Derives from', '{}'),
  ('/vocab/link/corroborates', 'link_type', 'Corroborates', '{}'),
  ('/vocab/link/contradicts', 'link_type', 'Contradicts', '{}'),
  ('/vocab/link/summarizes', 'link_type', 'Summarizes', '{}'),
  ('/vocab/link/duplicates', 'link_type', 'Duplicates', '{}'),
  ('/vocab/link/refines', 'link_type', 'Refines', '{}'),

  ('/vocab/evidence/primary', 'evidence_role', 'Primary evidence', '{}'),
  ('/vocab/evidence/supporting', 'evidence_role', 'Supporting evidence', '{}'),
  ('/vocab/evidence/contradictory', 'evidence_role', 'Contradictory evidence', '{}'),
  ('/vocab/evidence/excerpt', 'evidence_role', 'Excerpt / segment', '{}'),

  ('/vocab/metric/price.close', 'metric_type', 'Closing price', '{}'),
  ('/vocab/metric/temperature.celsius', 'metric_type', 'Temperature (Celsius)', '{}'),
  ('/vocab/metric/position.latitude', 'metric_type', 'Latitude', '{}'),
  ('/vocab/metric/position.longitude', 'metric_type', 'Longitude', '{}'),
  ('/vocab/metric/wind.speed_kmh', 'metric_type', 'Wind speed (km/h)', '{}'),
  ('/vocab/metric/token.amount', 'metric_type', 'Token transfer amount', '{}'),

  ('/vocab/geometry/boundary', 'geometry_type', 'Administrative boundary', '{}'),
  ('/vocab/geometry/footprint', 'geometry_type', 'Facility footprint', '{}'),
  ('/vocab/geometry/route', 'geometry_type', 'Route / track', '{}'),
  ('/vocab/geometry/coverage', 'geometry_type', 'Coverage area', '{}'),
  ('/vocab/geometry/center', 'geometry_type', 'Centroid point', '{}'),

  ('/vocab/embedding/text-embedding-3-small', 'embedding_model', 'OpenAI text-embedding-3-small', '{"dimensions":1536}'),
  ('/vocab/embedding/nomic-embed-text', 'embedding_model', 'Nomic embed text', '{"dimensions":768}'),

  ('/vocab/projection/signal', 'projection_type', 'Computed signal', '{}'),
  ('/vocab/projection/dossier', 'projection_type', 'Entity dossier cache', '{}'),
  ('/vocab/projection/timeseries', 'projection_type', 'Time-series rollup', '{}')
ON CONFLICT (uri) DO NOTHING;

COMMIT;
