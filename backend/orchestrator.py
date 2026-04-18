"""The orchestrator: takes an AnalyzeRequest, runs agents, refs with the runner.

M1 scope: one agent (null_guard / Codex). M2 will fan this out to 4 agents in
parallel and rank survivors by files_touched.

Every decision here is constrained by CLAUDE.md hard rules. In particular:
- No mocks. The runner is real pytest-in-Docker.
- Trace-before-fix. The prompt includes the stacktrace + frame source verbatim.
- One-shot per agent per episode. No retry loops.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

# Load .env.local from repo root so the CLI works even if the user hasn't exported.
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env.local", override=False)

from backend import hypotheses, supa  # noqa: E402
from backend.providers import (  # noqa: E402
    DEFAULT_OPENAI_MODEL,
    openai_codex_generate,
)
from contracts.schemas import AnalyzeRequest  # noqa: E402


RUNNER_IMAGE = os.environ.get("RUNNER_DOCKER_IMAGE", "redgreen-runner:dev")


# -------- runner invocation --------

@dataclass
class RunnerResult:
    status: str  # RED | GREEN | ERROR
    stdout: str
    duration_ms: int


def run_in_docker(*, episode_id: str, agent: str, test_code: str, patch: Optional[str], repo_snapshot_path: str) -> RunnerResult:
    payload = {
        "episode_id": episode_id,
        "agent": agent,
        "test_code": test_code,
        "patch_unified_diff": patch,
        "repo_snapshot_path": "/work",
    }
    cmd = [
        "docker", "run", "--rm", "--network", "none", "-i",
        "-v", f"{_REPO_ROOT / 'runner'}:/runner:ro",
        "-v", f"{repo_snapshot_path}:/work",
        RUNNER_IMAGE,
    ]
    proc = subprocess.run(cmd, input=json.dumps(payload), capture_output=True, text=True, timeout=90)
    if proc.returncode != 0 and not proc.stdout:
        return RunnerResult(status="ERROR", stdout=f"docker exit {proc.returncode}\n{proc.stderr[-2000:]}", duration_ms=0)
    try:
        obj = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return RunnerResult(status="ERROR", stdout=f"bad runner stdout: {proc.stdout[:500]}", duration_ms=0)
    return RunnerResult(status=obj["status"], stdout=obj.get("stdout", ""), duration_ms=int(obj.get("duration_ms", 0)))


def _prepare_snapshot(repo_path: pathlib.Path) -> pathlib.Path:
    """Copy the repo snapshot into a writable temp dir so the runner can patch it."""
    snap = pathlib.Path(tempfile.mkdtemp(prefix="redgreen-snap-"))
    shutil.copytree(repo_path, snap, dirs_exist_ok=True)
    return snap


# -------- 4-phase failure capture (CLAUDE.md hard rule #9 / ECC pattern) --------

def _log_header(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


# -------- episode runner (M1: one agent) --------

async def run_episode(request: AnalyzeRequest, *, agent: str = "null_guard", model: Optional[str] = None) -> dict:
    model = model or DEFAULT_OPENAI_MODEL

    episode_id = supa.insert_episode(
        repo_hash=request.repo_hash,
        frame_file=request.frame_file,
        frame_line=request.frame_line,
        stacktrace=request.stacktrace,
        notes=f"agent={agent} model={model}",
    )
    supa.upsert_agent(episode_id=episode_id, agent=agent, model=model, status="pending")

    started = time.monotonic()
    _log_header(f"episode {episode_id} — {agent} / {model}")

    gen = await openai_codex_generate(
        system=hypotheses.system_prompt(agent),
        user=hypotheses.user_prompt(
            stacktrace=request.stacktrace,
            frame_file=request.frame_file,
            frame_line=request.frame_line,
            frame_source=request.frame_source,
            locals_json=request.locals_json,
        ),
        model=model,
    )
    print(f"  model: {gen.get('elapsed_ms')}ms, in={gen.get('input_tokens')} out={gen.get('output_tokens')} err={gen.get('error')}")
    if gen.get("error") or not gen["test_code"] or not gen["patch"]:
        supa.upsert_agent(
            episode_id=episode_id, agent=agent, model=model,
            status="error", elapsed_ms=gen.get("elapsed_ms", 0),
            eliminated_reason=gen.get("error") or "empty test/patch",
            rationale=gen.get("rationale", ""),
        )
        supa.finalize_episode(episode_id=episode_id, state="no_winner", total_elapsed_ms=int((time.monotonic() - started) * 1000))
        return {"episode_id": episode_id, "winner": None, "reason": gen.get("error") or "empty test/patch"}

    # RED gate: fresh snapshot, no patch.
    snap_red = _prepare_snapshot(pathlib.Path(request.repo_snapshot_path))
    try:
        red = run_in_docker(episode_id=episode_id, agent=agent, test_code=gen["test_code"], patch=None, repo_snapshot_path=str(snap_red))
    finally:
        shutil.rmtree(snap_red, ignore_errors=True)
    print(f"  RED gate: {red.status} ({red.duration_ms}ms)")
    if red.status != "RED":
        supa.upsert_agent(
            episode_id=episode_id, agent=agent, model=model,
            status="red_failed", elapsed_ms=gen["elapsed_ms"] + red.duration_ms,
            eliminated_reason=f"RED gate returned {red.status}: {red.stdout[-500:]}",
            test_code=gen["test_code"], patch_unified_diff=gen["patch"], rationale=gen["rationale"],
        )
        supa.finalize_episode(episode_id=episode_id, state="no_winner", total_elapsed_ms=int((time.monotonic() - started) * 1000))
        return {"episode_id": episode_id, "winner": None, "reason": "red_failed", "stdout": red.stdout}

    # GREEN gate: fresh snapshot, apply patch.
    snap_green = _prepare_snapshot(pathlib.Path(request.repo_snapshot_path))
    try:
        green = run_in_docker(episode_id=episode_id, agent=agent, test_code=gen["test_code"], patch=gen["patch"], repo_snapshot_path=str(snap_green))
    finally:
        shutil.rmtree(snap_green, ignore_errors=True)
    print(f"  GREEN gate: {green.status} ({green.duration_ms}ms)")
    if green.status != "GREEN":
        supa.upsert_agent(
            episode_id=episode_id, agent=agent, model=model,
            status="green_failed", elapsed_ms=gen["elapsed_ms"] + red.duration_ms + green.duration_ms,
            eliminated_reason=f"GREEN gate returned {green.status}: {green.stdout[-500:]}",
            test_code=gen["test_code"], patch_unified_diff=gen["patch"], rationale=gen["rationale"],
        )
        supa.finalize_episode(episode_id=episode_id, state="no_winner", total_elapsed_ms=int((time.monotonic() - started) * 1000))
        return {"episode_id": episode_id, "winner": None, "reason": "green_failed", "stdout": green.stdout}

    total_elapsed = int((time.monotonic() - started) * 1000)
    files_touched = gen["patch"].count("\n+++ b/")
    supa.upsert_agent(
        episode_id=episode_id, agent=agent, model=model,
        status="green_ok", elapsed_ms=gen["elapsed_ms"] + red.duration_ms + green.duration_ms,
        files_touched=files_touched or 1,
        test_code=gen["test_code"], patch_unified_diff=gen["patch"], rationale=gen["rationale"],
    )
    supa.finalize_episode(
        episode_id=episode_id, state="completed",
        winner_agent=agent, winner_model=model, total_elapsed_ms=total_elapsed,
    )
    print(f"\nRED ✓ GREEN ✓ episode_id={episode_id} total_ms={total_elapsed}")
    return {
        "episode_id": episode_id,
        "winner": {"agent": agent, "model": model, "files_touched": files_touched or 1, "total_elapsed_ms": total_elapsed},
    }


# -------- CLI entry --------

def _load_seed_as_request(seed_name: str) -> AnalyzeRequest:
    """Build an AnalyzeRequest from a seed directory by running its crash.py."""
    seed_root = _REPO_ROOT / "seeds" / seed_name
    if not seed_root.exists():
        raise SystemExit(f"seed not found: {seed_root}")

    crash_script = seed_root / "crash.py"
    proc = subprocess.run(
        [sys.executable, str(crash_script)],
        cwd=str(seed_root), capture_output=True, text=True,
    )
    if proc.returncode == 0:
        raise SystemExit(f"{crash_script} exited 0 — seed is not reproducing its bug.")
    stacktrace = proc.stderr.strip() or proc.stdout.strip()

    # Pull frame_file + frame_line from the last `File "X", line N` in the trace.
    import re
    matches = re.findall(r'File "([^"]+)", line (\d+)', stacktrace)
    if not matches:
        raise SystemExit("could not parse stacktrace for frame file/line")
    frame_file_abs, frame_line_str = matches[-1]
    frame_file = os.path.relpath(frame_file_abs, seed_root) if frame_file_abs.startswith(str(seed_root)) else frame_file_abs
    frame_line = int(frame_line_str)

    # Grab ~40 lines around the failing line for context.
    src_path = pathlib.Path(frame_file_abs)
    src_lines = src_path.read_text().splitlines()
    lo = max(0, frame_line - 20)
    hi = min(len(src_lines), frame_line + 20)
    frame_source = "\n".join(f"{i+1:>4}: {src_lines[i]}" for i in range(lo, hi))

    repo_hash = _git_sha(seed_root) or f"seed:{seed_name}"

    return AnalyzeRequest(
        stacktrace=stacktrace,
        locals_json={},  # M1: skip real locals capture; M3 plugin side will fill.
        frame_file=frame_file,
        frame_line=frame_line,
        frame_source=frame_source,
        repo_hash=repo_hash,
        repo_snapshot_path=str(seed_root),
    )


def _git_sha(path: pathlib.Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, timeout=5,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


def _main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", required=True, help="seed name under seeds/ (e.g., null_guard)")
    ap.add_argument("--cli", action="store_true", help="run one episode from the CLI (no FastAPI)")
    ap.add_argument("--agent", default="null_guard")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    try:
        req = _load_seed_as_request(args.seed)
    except SystemExit as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    try:
        result = asyncio.run(run_episode(req, agent=args.agent, model=args.model))
    except Exception:
        traceback.print_exc()
        return 1

    if result.get("winner"):
        return 0
    print(f"no winner: {result.get('reason')}", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(_main())
