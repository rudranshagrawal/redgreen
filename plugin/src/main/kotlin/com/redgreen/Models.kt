package com.redgreen

/**
 * Kotlin mirrors of contracts/schemas.py. Any change here MUST be mirrored on
 * the Python side — the wire contract is frozen per CLAUDE.md rule #7.
 */

data class AnalyzePayload(
    val stacktrace: String,
    val locals_json: Map<String, Any?> = emptyMap(),
    val frame_file: String,
    val frame_line: Int,
    val frame_source: String,
    val repo_hash: String,
    val repo_snapshot_path: String,
    val codebase_context: String? = null,
)

data class AnalyzeResponse(val episode_id: String)

data class AgentResult(
    val agent: String,
    val model: String,
    val status: String,          // pending | red_ok | red_failed | green_ok | green_failed | regression_failed | error
    val elapsed_ms: Int,
    val files_touched: Int = 0,
    val eliminated_reason: String? = null,
    val cross_val_passed: Int = 0,
    val cross_val_failed: Int = 0,
    val regression_passed: Int = 0,
    val regression_failed: Int = 0,
)

data class Winner(
    val agent: String,
    val model: String,
    val test_code: String,
    val patch_unified_diff: String,
    val rationale: String,
    val files_touched: Int,
    val total_elapsed_ms: Int,
)

data class LeaderboardRow(
    val repo_hash: String,
    val agent: String,
    val wins: Int,
    val losses: Int,
    val avg_ms: Int,
)

data class StatusResponse(
    val episode_id: String,
    val state: String,            // racing | completed | no_winner
    val agents: List<AgentResult> = emptyList(),
    val winner: Winner? = null,
    val leaderboard_row: LeaderboardRow? = null,
)
