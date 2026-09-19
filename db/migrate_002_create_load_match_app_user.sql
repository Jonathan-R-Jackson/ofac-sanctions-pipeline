-- ============================================================
-- Migration: create the load_match_app database user and grant
-- it IAM authentication plus exactly the table access
-- load-match-lambda needs. Run once against the live RDS
-- instance.
--
-- Uses a dedicated app user rather than the postgres master
-- account, so the Lambda's IAM role can only do what this
-- pipeline actually needs, not everything the master account can.
-- ============================================================

BEGIN;

CREATE USER load_match_app;

-- Lets this user authenticate via IAM auth tokens instead of a
-- password, this is what the rds-db:connect IAM policy pairs with.
GRANT rds_iam TO load_match_app;

-- Full read/write on the tables this Lambda owns: upserting SDN
-- data, writing match results, and logging each run.
GRANT SELECT, INSERT, UPDATE, DELETE
    ON sdn_entities, sdn_aliases, sdn_addresses, screening_matches, pipeline_runs
    TO load_match_app;

-- Read-only on customers: this Lambda matches against them, it
-- doesn't create or modify them, that's Faker's job separately.
GRANT SELECT ON customers TO load_match_app;

-- Every SERIAL column's underlying sequence needs this too, or
-- INSERTs fail even with table-level INSERT granted, a common
-- gotcha when granting a non-owner role table access in Postgres.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO load_match_app;

COMMIT;
