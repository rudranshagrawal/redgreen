-- RedGreen Supabase schema.
-- Apply once in Supabase SQL Editor. Idempotent: safe to re-run.
-- Service-role key bypasses RLS, so we don't enable RLS for the hackathon.

create extension if not exists "pgcrypto";

-- ---------- enums ----------

do $$ begin
    create type agent_kind as enum ('null_guard', 'input_shape', 'async_race', 'config_drift');
exception when duplicate_object then null; end $$;

do $$ begin
    create type episode_state as enum ('racing', 'completed', 'no_winner');
exception when duplicate_object then null; end $$;

do $$ begin
    create type agent_status as enum (
        'pending',
        'red_ok',
        'red_failed',
        'green_ok',
        'green_failed',
        'error'
    );
exception when duplicate_object then null; end $$;

-- ---------- episodes ----------

create table if not exists episodes (
    id              uuid primary key default gen_random_uuid(),
    created_at      timestamptz not null default now(),
    repo_hash       text        not null,
    frame_file      text        not null,
    frame_line      int         not null,
    stacktrace      text        not null,
    state           episode_state not null default 'racing',
    winner_agent    agent_kind,
    winner_model    text,
    total_elapsed_ms int        default 0,
    notes           text
);

create index if not exists episodes_repo_hash_idx on episodes (repo_hash, created_at desc);
create index if not exists episodes_state_idx on episodes (state);

-- ---------- agents (one row per agent per episode) ----------

create table if not exists agents (
    id                 uuid primary key default gen_random_uuid(),
    episode_id         uuid not null references episodes(id) on delete cascade,
    agent              agent_kind   not null,
    model              text         not null,
    status             agent_status not null default 'pending',
    elapsed_ms         int          not null default 0,
    files_touched      int          not null default 0,
    eliminated_reason  text,
    test_code          text,
    patch_unified_diff text,
    rationale          text,
    created_at         timestamptz  not null default now(),
    unique (episode_id, agent)
);

create index if not exists agents_episode_idx on agents (episode_id);
create index if not exists agents_agent_idx on agents (agent, status);

-- ---------- leaderboard (per-repo, per-agent stats) ----------

create or replace view leaderboard as
select
    e.repo_hash,
    a.agent,
    count(*) filter (where a.status = 'green_ok') as wins,
    count(*) filter (where a.status in ('red_failed','green_failed','error')) as losses,
    coalesce(round(avg(a.elapsed_ms) filter (where a.status = 'green_ok'))::int, 0) as avg_ms,
    count(*) as total_attempts
from agents a
join episodes e on e.id = a.episode_id
group by e.repo_hash, a.agent;

-- ---------- quick self-check ----------
-- select * from episodes limit 1;
-- select * from agents   limit 1;
-- select * from leaderboard;
