-- Schema v4: regression gate. Records pre-existing-test results when each
-- candidate patch is applied in isolation. Used to disqualify patches that
-- fix the target bug but break unrelated features.
--
-- Safe to re-run. Uses IF NOT EXISTS so partial application is harmless.

ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS regression_passed int NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS regression_failed int NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS regression_ms int NOT NULL DEFAULT 0;

-- Extend the status CHECK constraint (if one exists) to allow the new
-- 'regression_failed' state. If your schema didn't originally pin status to
-- an enum/check, this is a no-op — the column is already permissive.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'agents' AND constraint_name = 'agents_status_check'
    ) THEN
        ALTER TABLE agents DROP CONSTRAINT agents_status_check;
    END IF;
    ALTER TABLE agents ADD CONSTRAINT agents_status_check
        CHECK (status IN (
            'pending', 'red_ok', 'red_failed',
            'green_ok', 'green_failed',
            'regression_failed', 'error'
        ));
END
$$;
