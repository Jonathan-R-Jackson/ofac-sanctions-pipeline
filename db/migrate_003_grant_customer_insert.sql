-- ============================================================
-- Migration: grant load_match_app INSERT on customers.
--
-- migrate_002 deliberately granted only SELECT here, reasoning
-- that this Lambda matches against customers but doesn't create
-- them. That was true until the daily customer seeding feature
-- was added (see daily_seed.py), which does insert new customers
-- from inside this same Lambda. This grant should have been part
-- of that change and wasn't; fixing it now.
-- ============================================================

BEGIN;

GRANT INSERT ON customers TO load_match_app;

COMMIT;
