-- ============================================================
-- Migration: expand sdn_addresses with fields confirmed present
-- in a real production file (city, address1-3, region, postal
-- code), which the original schema.sql didn't capture since only
-- country was visible in the documentation's worked example at
-- the time it was written.
--
-- Run this once against the existing live RDS instance. New
-- fresh applies of schema.sql already include these columns, so
-- this file is only needed to catch an already-provisioned
-- database up to match.
-- ============================================================

BEGIN;

ALTER TABLE sdn_addresses
    ADD COLUMN IF NOT EXISTS address1 TEXT,
    ADD COLUMN IF NOT EXISTS address2 TEXT,
    ADD COLUMN IF NOT EXISTS address3 TEXT,
    ADD COLUMN IF NOT EXISTS city TEXT,
    ADD COLUMN IF NOT EXISTS region TEXT,
    ADD COLUMN IF NOT EXISTS postal_code TEXT;

COMMENT ON COLUMN sdn_addresses.country IS
    'From address/country.';
COMMENT ON COLUMN sdn_addresses.city IS
    'From the primary translation''s addressParts. Confirmed against a real production file: populated on well over half of all address records (17,007 of ~19,400 entities), not an edge case.';
COMMENT ON COLUMN sdn_addresses.address1 IS
    'From addressParts type ADDRESS1. Confirmed present on 14,251 of ~19,400 entities in a real production file.';

COMMIT;
