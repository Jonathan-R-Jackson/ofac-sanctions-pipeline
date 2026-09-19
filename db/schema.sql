-- ============================================================
-- OFAC Sanctions Screening Pipeline: Schema
-- Target: AWS RDS Postgres
--
-- Field definitions match DATA_DICTIONARY.md; that document is
-- the source of truth if this ever drifts from it. Applied
-- manually for v1 (no migration framework yet; see Test Plan,
-- CI/CD section).
--
-- Design note on delisting: OFAC occasionally removes entities
-- from the SDN list. This schema does not delete rows from
-- sdn_entities when that happens. The load step only inserts
-- new entities and updates existing ones by ent_num; a delisted
-- entity's row is left in place. Silently deleting sanctions
-- history is the wrong default here. An explicit is_active flag
-- for delisting is a plausible v2, not implemented in v1.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- sdn_entities
-- One row per SDN entity. ent_num is OFAC's own identifier and
-- the upsert key: re-running the load updates existing rows by
-- ent_num rather than duplicating them.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sdn_entities (
    ent_num             INTEGER PRIMARY KEY,
    identity_id         INTEGER,
    entity_type         TEXT NOT NULL
                            CONSTRAINT chk_sdn_entities_entity_type
                            CHECK (entity_type IN ('Individual', 'Entity', 'Vessel', 'Aircraft')),
    primary_name        TEXT NOT NULL,
    sanctions_programs  TEXT[] NOT NULL,
    date_of_birth       DATE,
    gender              TEXT,
    remarks             TEXT,
    loaded_at           TIMESTAMP NOT NULL DEFAULT now()
);

COMMENT ON TABLE sdn_entities IS
    'One row per OFAC SDN entity, sourced from GET /entities?list=SDN List.';
COMMENT ON COLUMN sdn_entities.ent_num IS
    'OFAC entity/@id. Upsert key.';
COMMENT ON COLUMN sdn_entities.sanctions_programs IS
    'An entity can carry more than one program (e.g. {CUBA,SDGT}); see Data Dictionary Key Decisions.';
COMMENT ON COLUMN sdn_entities.remarks IS
    'Not confirmed to exist on the REST response as of this writing; see Data Dictionary Open Items. Left NULL if absent from the source.';

CREATE INDEX IF NOT EXISTS idx_sdn_entities_programs
    ON sdn_entities USING GIN (sanctions_programs);

-- ------------------------------------------------------------
-- sdn_aliases
-- One row per non-primary name entry (A.K.A., F.K.A., N.K.A.)
-- on an entity.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sdn_aliases (
    id              SERIAL PRIMARY KEY,
    ent_num         INTEGER NOT NULL
                        CONSTRAINT fk_sdn_aliases_ent_num
                        REFERENCES sdn_entities(ent_num) ON DELETE CASCADE,
    alias_name      TEXT NOT NULL,
    alias_type      TEXT,
    is_low_quality  BOOLEAN NOT NULL DEFAULT false
);

COMMENT ON COLUMN sdn_aliases.is_low_quality IS
    'From API names[].isLowQuality. Not used to filter matches in v1; candidate for down-weighting in v2.';

CREATE INDEX IF NOT EXISTS idx_sdn_aliases_ent_num ON sdn_aliases(ent_num);

-- ------------------------------------------------------------
-- sdn_addresses
-- One row per address entry on an entity.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sdn_addresses (
    id           SERIAL PRIMARY KEY,
    ent_num      INTEGER NOT NULL
                    CONSTRAINT fk_sdn_addresses_ent_num
                    REFERENCES sdn_entities(ent_num) ON DELETE CASCADE,
    country      TEXT,
    address1     TEXT,
    address2     TEXT,
    address3     TEXT,
    city         TEXT,
    region       TEXT,
    postal_code  TEXT
);

COMMENT ON COLUMN sdn_addresses.country IS
    'From address/country.';
COMMENT ON COLUMN sdn_addresses.city IS
    'From the primary translation''s addressParts. Confirmed against a real production file: populated on well over half of all address records (17,007 of ~19,400 entities), not an edge case.';
COMMENT ON COLUMN sdn_addresses.address1 IS
    'From addressParts type ADDRESS1. Confirmed present on 14,251 of ~19,400 entities in a real production file.';

CREATE INDEX IF NOT EXISTS idx_sdn_addresses_ent_num ON sdn_addresses(ent_num);

-- ------------------------------------------------------------
-- customers
-- Synthetic test population only. Never real customer data;
-- see DRD non-goals.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
    id                  SERIAL PRIMARY KEY,
    full_name           TEXT NOT NULL,
    date_of_birth       DATE,
    country             TEXT,
    is_seeded_positive  BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMP NOT NULL DEFAULT now()
);

COMMENT ON TABLE customers IS
    'Synthetic test population only; see DRD non-goals. Never real customer PII.';
COMMENT ON COLUMN customers.date_of_birth IS
    'Generated but not used in v1 matching (name-only); available for a v2 DOB-assisted match.';
COMMENT ON COLUMN customers.is_seeded_positive IS
    'Flags rows deliberately inserted as true-positive test cases; see Test Plan.';

-- ------------------------------------------------------------
-- screening_matches
-- One row per customer-to-entity match above the score
-- threshold.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS screening_matches (
    id            SERIAL PRIMARY KEY,
    customer_id   INTEGER NOT NULL
                    CONSTRAINT fk_screening_matches_customer_id
                    REFERENCES customers(id) ON DELETE CASCADE,
    ent_num       INTEGER NOT NULL
                    CONSTRAINT fk_screening_matches_ent_num
                    REFERENCES sdn_entities(ent_num) ON DELETE RESTRICT,
    matched_name  TEXT NOT NULL,
    matched_on    TEXT NOT NULL
                    CONSTRAINT chk_screening_matches_matched_on
                    CHECK (matched_on IN ('primary_name', 'alias')),
    match_score   NUMERIC(5,2) NOT NULL
                    CONSTRAINT chk_screening_matches_score_range
                    CHECK (match_score BETWEEN 0 AND 100),
    status        TEXT NOT NULL DEFAULT 'pending_review'
                    CONSTRAINT chk_screening_matches_status
                    CHECK (status IN ('pending_review', 'cleared', 'flagged')),
    screened_at   TIMESTAMP NOT NULL DEFAULT now()
);

COMMENT ON COLUMN screening_matches.matched_name IS
    'The specific name/alias text that scored the match, since a customer can match more than one alias of the same entity.';
COMMENT ON CONSTRAINT fk_screening_matches_ent_num ON screening_matches IS
    'RESTRICT, not CASCADE: match history must survive even if an entity is later removed from sdn_entities.';

CREATE INDEX IF NOT EXISTS idx_screening_matches_customer_id ON screening_matches(customer_id);
CREATE INDEX IF NOT EXISTS idx_screening_matches_ent_num ON screening_matches(ent_num);

-- ------------------------------------------------------------
-- pipeline_runs
-- One row per pipeline execution (Extract Lambda + Load/Match
-- Lambda together count as one run).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id                 SERIAL PRIMARY KEY,
    started_at         TIMESTAMP NOT NULL,
    completed_at       TIMESTAMP,
    status             TEXT NOT NULL
                        CONSTRAINT chk_pipeline_runs_status
                        CHECK (status IN ('running', 'success', 'failed')),
    entities_pulled    INTEGER,
    entities_upserted  INTEGER,
    matches_found      INTEGER,
    error_message      TEXT
);

COMMIT;
