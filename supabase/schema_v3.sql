-- RedGreen schema v3 — persist cross-validation stats per agent.
-- Run once in Supabase SQL Editor after v2. Idempotent.
--
-- The winner of each episode is picked by (judge verdict, cross_val_passed
-- desc, files_touched asc, elapsed asc). Surfacing the cross-val numbers on
-- the agent row lets the plugin UI show "GREEN ✓ · 13/14 peer tests" instead
-- of just "GREEN ✓ passed", which is the differentiating claim of the product.

alter table agents add column if not exists cross_val_passed int not null default 0;
alter table agents add column if not exists cross_val_failed int not null default 0;

-- No backfill needed — rows that predate this schema keep 0/0, which the
-- plugin renders as "no cross-val data" cleanly.
