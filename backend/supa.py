"""Thin Supabase writer. Service-role key — bypasses RLS.

Writes:
  - insert_episode: one row into `episodes`
  - upsert_agent:   one row into `agents` (unique per (episode_id, agent))
  - finalize_episode: updates episode state + winner
"""

from __future__ import annotations

import os
from typing import Any

from supabase import Client, create_client


_client: Client | None = None


def client() -> Client:
    global _client
    if _client is None:
        _client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        )
    return _client


def insert_episode(*, repo_hash: str, frame_file: str, frame_line: int, stacktrace: str, notes: str = "") -> str:
    payload = {
        "repo_hash": repo_hash,
        "frame_file": frame_file,
        "frame_line": frame_line,
        "stacktrace": stacktrace,
        "state": "racing",
        "notes": notes or None,
    }
    res = client().table("episodes").insert(payload).execute()
    return res.data[0]["id"]


def upsert_agent(
    *,
    episode_id: str,
    agent: str,
    model: str,
    status: str,
    elapsed_ms: int = 0,
    files_touched: int = 0,
    eliminated_reason: str | None = None,
    test_code: str | None = None,
    patch_unified_diff: str | None = None,
    rationale: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "episode_id": episode_id,
        "agent": agent,
        "model": model,
        "status": status,
        "elapsed_ms": elapsed_ms,
        "files_touched": files_touched,
        "eliminated_reason": eliminated_reason,
        "test_code": test_code,
        "patch_unified_diff": patch_unified_diff,
        "rationale": rationale,
    }
    client().table("agents").upsert(payload, on_conflict="episode_id,agent").execute()


def finalize_episode(
    *,
    episode_id: str,
    state: str,
    winner_agent: str | None = None,
    winner_model: str | None = None,
    total_elapsed_ms: int = 0,
) -> None:
    client().table("episodes").update(
        {
            "state": state,
            "winner_agent": winner_agent,
            "winner_model": winner_model,
            "total_elapsed_ms": total_elapsed_ms,
        }
    ).eq("id", episode_id).execute()


def read_leaderboard(repo_hash: str) -> list[dict]:
    res = client().table("leaderboard").select("*").eq("repo_hash", repo_hash).execute()
    return res.data or []
