"""Wire contracts for RedGreen. FROZEN at M0, extended at M5.

Plugin and backend implement to these schemas so neither side blocks on the
other. The runner is internal to the backend but shares the same Pydantic
definitions for safety.

If a field truly must change, bump SCHEMA_VERSION and update both sides in a
single commit.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = "3"

# Agent catalog — the 4 originals plus 8 M5-era hypotheses. A dynamic
# router selects up to 4 of these per episode based on the stacktrace.
Agent = Literal[
    "null_guard",
    "input_shape",
    "async_race",
    "config_drift",
    "math_error",
    "resource_leak",
    "encoding",
    "recursion",
    "api_contract",
    "timezone",
    "auth_permission",
    "dependency_missing",
]
RunStatus = Literal["RED", "GREEN", "REGRESSION_FAILED", "ERROR"]
EpisodeState = Literal["racing", "completed", "no_winner"]


# ---------- Plugin -> Backend ----------

class AnalyzeRequest(BaseModel):
    stacktrace: str
    locals_json: dict = Field(
        default_factory=dict,
        description="Serialized local vars from the failing frame.",
    )
    frame_file: str
    frame_line: int
    frame_source: str = Field(
        description="~40 lines of source centered on the failing line.",
    )
    repo_hash: str = Field(description="git sha of the project being debugged.")
    repo_snapshot_path: str = Field(
        description="Local path the runner can mount read-only.",
    )
    codebase_context: Optional[str] = Field(
        default=None,
        description="Optional summary of project conventions (imports, exceptions, test style, etc.) produced by the plugin's codebase indexer.",
    )


class AnalyzeResponse(BaseModel):
    episode_id: str


# ---------- Backend -> Runner (internal) ----------

class RunRequest(BaseModel):
    episode_id: str
    agent: Agent
    test_code: str
    patch_unified_diff: Optional[str] = Field(
        default=None,
        description="None for the RED gate (reproduce bug); unified diff for the GREEN gate.",
    )
    repo_snapshot_path: str


class RunResponse(BaseModel):
    status: RunStatus
    stdout: str
    duration_ms: int


# ---------- Plugin <- Backend (poll) ----------

class AgentResult(BaseModel):
    agent: Agent
    model: str
    status: Literal[
        "pending", "red_ok", "red_failed", "green_ok", "green_failed",
        "regression_failed", "error",
    ]
    elapsed_ms: int = 0
    eliminated_reason: Optional[str] = None
    files_touched: int = 0
    cross_val_passed: int = 0
    cross_val_failed: int = 0
    regression_passed: int = 0
    regression_failed: int = 0


class Winner(BaseModel):
    agent: Agent
    model: str
    test_code: str
    patch_unified_diff: str
    rationale: str
    files_touched: int
    total_elapsed_ms: int


class LeaderboardRow(BaseModel):
    repo_hash: str
    agent: Agent
    wins: int
    losses: int
    avg_ms: int


class StatusResponse(BaseModel):
    episode_id: str
    state: EpisodeState
    agents: list[AgentResult]
    winner: Optional[Winner] = None
    leaderboard_row: Optional[LeaderboardRow] = None
