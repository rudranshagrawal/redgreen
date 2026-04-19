-- RedGreen schema v2 — expands agent_kind with 8 new hypothesis lenses.
-- Run once in Supabase SQL Editor after v1. Idempotent.
--
-- Existing episodes/agents rows keep working unchanged; the old 4 enum
-- values are still valid and still populated.

alter type agent_kind add value if not exists 'math_error';
alter type agent_kind add value if not exists 'resource_leak';
alter type agent_kind add value if not exists 'encoding';
alter type agent_kind add value if not exists 'recursion';
alter type agent_kind add value if not exists 'api_contract';
alter type agent_kind add value if not exists 'timezone';
alter type agent_kind add value if not exists 'auth_permission';
alter type agent_kind add value if not exists 'dependency_missing';

-- Sanity:
--   select unnest(enum_range(null::agent_kind));
