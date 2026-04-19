-- Schema v4: regression gate. Records pre-existing-test results when each
-- candidate patch is applied in isolation. Used to disqualify patches that
-- fix the target bug but break unrelated features.
--
-- Safe to re-run. Uses IF NOT EXISTS / ADD VALUE IF NOT EXISTS so partial
-- application is harmless.

-- 1. Extend the agent_status enum with the new state.
--    Postgres 12+ supports `ADD VALUE IF NOT EXISTS`. Must run outside a
--    transaction block, so don't wrap this file in BEGIN/COMMIT.
ALTER TYPE agent_status ADD VALUE IF NOT EXISTS 'regression_failed';

-- 2. Add the three regression columns to `agents`.
ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS regression_passed int NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS regression_failed int NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS regression_ms int NOT NULL DEFAULT 0;
