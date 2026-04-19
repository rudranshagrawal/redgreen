"""FastAPI surface for the IDE plugin.

Two endpoints:

    POST /analyze  -> {"episode_id": "..."}   (starts background race)
    GET  /status/{episode_id} -> StatusResponse

The plugin POSTs an AnalyzeRequest, gets an id back immediately, then polls
/status until state != "racing". No websockets, no streaming — polling is
the simplest thing that survives firewalls and IDE threading models.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import time
from typing import Optional

from dotenv import load_dotenv

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env.local", override=False)

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from backend import orchestrator, supa  # noqa: E402
from contracts.schemas import (  # noqa: E402
    AgentResult,
    AnalyzeRequest,
    AnalyzeResponse,
    EpisodeState,
    LeaderboardRow,
    StatusResponse,
    Winner,
)


app = FastAPI(title="RedGreen Backend", version="0.1")

# Permissive CORS — dev only. The plugin connects from localhost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# In-memory episode state. Source of truth is still Supabase; this dict lets
# the plugin poll without hitting Supabase on every tick.
_EPISODES: dict[str, dict] = {}


async def _run_background(request: AnalyzeRequest, placeholder_id: str) -> None:
    try:
        result = await orchestrator.run_episode(request)
        _EPISODES[placeholder_id] = {"done": True, **result}
    except Exception as e:  # noqa: BLE001
        _EPISODES[placeholder_id] = {"done": True, "error": str(e), "winner": None, "outcomes": []}


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    # Placeholder id: we overwrite once the orchestrator returns the real Supabase id.
    placeholder = f"pending-{int(time.time() * 1000)}"
    _EPISODES[placeholder] = {"done": False, "started_at": time.time()}
    asyncio.create_task(_run_background(request, placeholder))
    return AnalyzeResponse(episode_id=placeholder)


def _supabase_status(episode_id: str) -> Optional[StatusResponse]:
    """Fetch episode + agent rows from Supabase and project onto StatusResponse."""
    ep_rows = supa.client().table("episodes").select("*").eq("id", episode_id).execute().data
    if not ep_rows:
        return None
    ep = ep_rows[0]
    agent_rows = supa.client().table("agents").select("*").eq("episode_id", episode_id).execute().data

    agents = [
        AgentResult(
            agent=r["agent"],
            model=r["model"],
            status=r["status"],
            elapsed_ms=r.get("elapsed_ms", 0) or 0,
            eliminated_reason=r.get("eliminated_reason"),
            files_touched=r.get("files_touched", 0) or 0,
            cross_val_passed=r.get("cross_val_passed") or 0,
            cross_val_failed=r.get("cross_val_failed") or 0,
        )
        for r in agent_rows
    ]

    winner: Optional[Winner] = None
    if ep["state"] == "completed" and ep.get("winner_agent"):
        win_row = next((r for r in agent_rows if r["agent"] == ep["winner_agent"]), None)
        if win_row:
            winner = Winner(
                agent=win_row["agent"],
                model=win_row["model"],
                test_code=win_row.get("test_code") or "",
                patch_unified_diff=win_row.get("patch_unified_diff") or "",
                rationale=win_row.get("rationale") or "",
                files_touched=win_row.get("files_touched", 1) or 1,
                total_elapsed_ms=ep.get("total_elapsed_ms", 0) or 0,
            )

    leaderboard_row: Optional[LeaderboardRow] = None
    lb = supa.read_leaderboard(ep["repo_hash"])
    if lb and ep.get("winner_agent"):
        for row in lb:
            if row["agent"] == ep["winner_agent"]:
                leaderboard_row = LeaderboardRow(
                    repo_hash=row["repo_hash"], agent=row["agent"],
                    wins=row["wins"], losses=row["losses"], avg_ms=row["avg_ms"],
                )
                break

    return StatusResponse(
        episode_id=episode_id,
        state=ep["state"],
        agents=agents,
        winner=winner,
        leaderboard_row=leaderboard_row,
    )


@app.get("/status/{episode_id}", response_model=StatusResponse)
async def status(episode_id: str) -> StatusResponse:
    # If still a pending-<ts> placeholder, read from in-memory.
    if episode_id.startswith("pending-"):
        mem = _EPISODES.get(episode_id)
        if not mem:
            raise HTTPException(404, "unknown episode")
        if not mem.get("done"):
            return StatusResponse(
                episode_id=episode_id,
                state="racing",
                agents=[],
            )
        if mem.get("error"):
            return StatusResponse(
                episode_id=episode_id,
                state="no_winner",
                agents=[],
            )
        real_id = mem.get("episode_id")
        if not real_id:
            return StatusResponse(episode_id=episode_id, state="no_winner", agents=[])
        snap = _supabase_status(real_id)
        if snap is None:
            raise HTTPException(404, "episode vanished from supabase")
        # Swap id so the client stops polling the placeholder.
        return snap

    snap = _supabase_status(episode_id)
    if snap is None:
        raise HTTPException(404, "unknown episode")
    return snap


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
