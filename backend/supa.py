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
    cross_val_passed: int | None = None,
    cross_val_failed: int | None = None,
    regression_passed: int | None = None,
    regression_failed: int | None = None,
    regression_ms: int | None = None,
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
    if cross_val_passed is not None:
        payload["cross_val_passed"] = cross_val_passed
    if cross_val_failed is not None:
        payload["cross_val_failed"] = cross_val_failed
    if regression_passed is not None:
        payload["regression_passed"] = regression_passed
    if regression_failed is not None:
        payload["regression_failed"] = regression_failed
    if regression_ms is not None:
        payload["regression_ms"] = regression_ms
    try:
        client().table("agents").upsert(payload, on_conflict="episode_id,agent").execute()
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        lower = msg.lower()
        # Schema v4 not applied yet? Drop regression fields and retry.
        if "regression" in msg and ("column" in lower or "does not exist" in lower):
            payload.pop("regression_passed", None)
            payload.pop("regression_failed", None)
            payload.pop("regression_ms", None)
            try:
                client().table("agents").upsert(payload, on_conflict="episode_id,agent").execute()
                return
            except Exception as e2:  # noqa: BLE001
                msg = str(e2)
                lower = msg.lower()
        # Schema v3 not applied? Drop cross-val too and retry once more. Also
        # drop any stray regression fields so we don't re-trip the v4 branch.
        if "cross_val" in msg and ("column" in lower or "does not exist" in lower):
            payload.pop("cross_val_passed", None)
            payload.pop("cross_val_failed", None)
            payload.pop("regression_passed", None)
            payload.pop("regression_failed", None)
            payload.pop("regression_ms", None)
            client().table("agents").upsert(payload, on_conflict="episode_id,agent").execute()
        else:
            raise


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


def read_winner_history(repo_hash: str) -> dict[tuple[str, str], int]:
    """Return {(winner_agent, winner_model): win_count} for all completed
    episodes on this codebase. Used by select_agents_for to bias the model
    roster toward historical winners — the "episode 20 picks the right
    model first try" claim.
    """
    from collections import defaultdict
    res = (
        client().table("episodes")
        .select("winner_agent, winner_model")
        .eq("repo_hash", repo_hash)
        .eq("state", "completed")
        .execute()
    )
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in res.data or []:
        agent = row.get("winner_agent")
        model = row.get("winner_model")
        if agent and model:
            counts[(agent, model)] += 1
    return dict(counts)
