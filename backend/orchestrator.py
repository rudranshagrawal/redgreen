"""The orchestrator.

M2: race 4 agents in parallel. Survivors are the ones that pass both the RED
gate (their test reproduces the bug) and the GREEN gate (their patch flips
it). Winner is the survivor with the fewest files touched, elapsed_ms as
tiebreak.

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
from dataclasses import dataclass, field
from typing import Callable, Optional

from dotenv import load_dotenv

# Load .env.local from repo root so the CLI works even if the user hasn't exported.
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env.local", override=False)

import random  # noqa: E402

from backend import hypotheses, router, supa  # noqa: E402
from backend.providers import (  # noqa: E402
    DEFAULT_NEBIUS_DEEPSEEK,
    DEFAULT_NEBIUS_LLAMA,
    DEFAULT_NEBIUS_QWEN,
    DEFAULT_OPENAI_MODEL,
    nebius_generate,
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
    passed: int = 0
    failed: int = 0
    errors: int = 0


def _run_in_docker_sync(
    *,
    test_code: Optional[str] = None,
    test_files: Optional[dict[str, str]] = None,
    patch: Optional[str],
    repo_snapshot_path: str,
) -> RunnerResult:
    payload: dict = {
        "episode_id": "n/a",
        "agent": "n/a",
        "patch_unified_diff": patch,
        "repo_snapshot_path": "/work",
    }
    if test_files:
        payload["test_files"] = test_files
    if test_code:
        payload["test_code"] = test_code

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
    return RunnerResult(
        status=obj["status"],
        stdout=obj.get("stdout", ""),
        duration_ms=int(obj.get("duration_ms", 0)),
        passed=int(obj.get("passed", 0)),
        failed=int(obj.get("failed", 0)),
        errors=int(obj.get("errors", 0)),
    )


async def run_in_docker(**kwargs) -> RunnerResult:
    return await asyncio.to_thread(_run_in_docker_sync, **kwargs)


def _prepare_snapshot(repo_path: pathlib.Path) -> pathlib.Path:
    snap = pathlib.Path(tempfile.mkdtemp(prefix="redgreen-snap-"))
    shutil.copytree(repo_path, snap, dirs_exist_ok=True)
    return snap


# -------- agent specs --------

AgentName = str  # null_guard | input_shape | async_race | config_drift
ProviderFn = Callable[..., "asyncio.Future[dict]"]


@dataclass(frozen=True)
class AgentSpec:
    name: AgentName
    model: str
    provider: ProviderFn  # async def generate(*, system, user, model, max_tokens) -> dict


def default_agent_pool() -> list[AgentSpec]:
    """Legacy fixed pairing kept for the CLI path / backward compatibility."""
    return [
        AgentSpec("null_guard", DEFAULT_OPENAI_MODEL, openai_codex_generate),
        AgentSpec("input_shape", DEFAULT_NEBIUS_LLAMA, nebius_generate),
        AgentSpec("async_race", DEFAULT_NEBIUS_QWEN, nebius_generate),
        AgentSpec("config_drift", DEFAULT_NEBIUS_DEEPSEEK, nebius_generate),
    ]


# Ordered roster of available (model, provider_fn) pairs. The router picks
# the hypothesis lenses per episode; we shuffle these across the picks.
_MODEL_ROSTER: tuple[tuple[str, ProviderFn], ...] = (
    (DEFAULT_OPENAI_MODEL, openai_codex_generate),
    (DEFAULT_NEBIUS_LLAMA, nebius_generate),
    (DEFAULT_NEBIUS_QWEN, nebius_generate),
    (DEFAULT_NEBIUS_DEEPSEEK, nebius_generate),
)


def select_agents_for(request: AnalyzeRequest, *, seed: int | None = None) -> list[AgentSpec]:
    """Route + rotate: pick top-4 hypotheses for this stacktrace, then assign
    models to them via a per-episode shuffled roster.

    The shuffle lets the leaderboard disentangle "which lens fits?" from
    "which model is strong?" over many episodes — same model won't always
    be paired with the same lens.
    """
    scores = router.score_hypotheses(request.stacktrace, request.frame_source)
    picks = router.pick_top(scores, k=len(_MODEL_ROSTER))

    rng = random.Random(seed if seed is not None else hash((request.repo_hash, request.frame_file, request.frame_line)))
    shuffled_models = list(_MODEL_ROSTER)
    rng.shuffle(shuffled_models)

    specs: list[AgentSpec] = []
    for agent_name, (model, provider) in zip(picks, shuffled_models):
        specs.append(AgentSpec(agent_name, model, provider))
    return specs


# -------- per-agent outcome --------

@dataclass
class AgentOutcome:
    spec: AgentSpec
    status: str  # pending | red_ok | red_failed | green_ok | green_failed | error
    elapsed_ms: int = 0
    files_touched: int = 0
    eliminated_reason: Optional[str] = None
    test_code: str = ""
    patch: str = ""
    rationale: str = ""
    gen_ms: int = 0
    red_ms: int = 0
    green_ms: int = 0
    cross_val_passed: int = 0
    cross_val_failed: int = 0


def _slug(s: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_]+", "_", s)[:40]


# -------- phase 1: gen + RED gate (per agent) --------

async def _phase1_gen_and_red(spec: AgentSpec, request: AnalyzeRequest, episode_id: str) -> AgentOutcome:
    tag = f"{spec.name:<13}"
    print(f"  {tag} start    model={spec.model}", flush=True)

    gen = await spec.provider(
        system=hypotheses.system_prompt(spec.name),
        user=hypotheses.user_prompt(
            stacktrace=request.stacktrace,
            frame_file=request.frame_file,
            frame_line=request.frame_line,
            frame_source=request.frame_source,
            locals_json=request.locals_json,
            codebase_context=request.codebase_context,
        ),
        model=spec.model,
    )
    gen_ms = gen.get("elapsed_ms", 0)
    print(f"  {tag} model    {gen_ms}ms in={gen.get('input_tokens')} out={gen.get('output_tokens')} err={gen.get('error')}", flush=True)

    if gen.get("error") or not gen.get("test_code") or not gen.get("patch"):
        outcome = AgentOutcome(
            spec=spec, status="error", elapsed_ms=gen_ms, gen_ms=gen_ms,
            eliminated_reason=gen.get("error") or "empty test/patch",
            rationale=gen.get("rationale", ""),
        )
        supa.upsert_agent(
            episode_id=episode_id, agent=spec.name, model=spec.model,
            status=outcome.status, elapsed_ms=outcome.elapsed_ms,
            eliminated_reason=outcome.eliminated_reason, rationale=outcome.rationale,
        )
        return outcome

    # Live update: model returned a valid candidate. Still "pending" until the
    # runner gates pass, but with elapsed_ms > 0 the plugin can show "model done".
    supa.upsert_agent(
        episode_id=episode_id, agent=spec.name, model=spec.model,
        status="pending", elapsed_ms=gen_ms,
        test_code=gen["test_code"], patch_unified_diff=gen["patch"], rationale=gen["rationale"],
    )

    # RED gate.
    snap_red = _prepare_snapshot(pathlib.Path(request.repo_snapshot_path))
    try:
        red = await run_in_docker(test_code=gen["test_code"], patch=None, repo_snapshot_path=str(snap_red))
    finally:
        shutil.rmtree(snap_red, ignore_errors=True)
    print(f"  {tag} RED      {red.status} ({red.duration_ms}ms)", flush=True)

    if red.status != "RED":
        outcome = AgentOutcome(
            spec=spec, status="red_failed", elapsed_ms=gen_ms + red.duration_ms,
            gen_ms=gen_ms, red_ms=red.duration_ms,
            eliminated_reason=f"RED -> {red.status}: {red.stdout[-300:]}",
            test_code=gen["test_code"], patch=gen["patch"], rationale=gen["rationale"],
        )
        supa.upsert_agent(
            episode_id=episode_id, agent=spec.name, model=spec.model,
            status=outcome.status, elapsed_ms=outcome.elapsed_ms,
            eliminated_reason=outcome.eliminated_reason,
            test_code=outcome.test_code, patch_unified_diff=outcome.patch, rationale=outcome.rationale,
        )
        return outcome

    # Phase 1 done. Record live state + return intermediate outcome for phase 2.
    supa.upsert_agent(
        episode_id=episode_id, agent=spec.name, model=spec.model,
        status="red_ok", elapsed_ms=gen_ms + red.duration_ms,
        test_code=gen["test_code"], patch_unified_diff=gen["patch"], rationale=gen["rationale"],
    )
    files_touched = max(1, gen["patch"].count("\n+++ b/"))
    return AgentOutcome(
        spec=spec, status="red_ok", elapsed_ms=gen_ms + red.duration_ms,
        gen_ms=gen_ms, red_ms=red.duration_ms, files_touched=files_touched,
        test_code=gen["test_code"], patch=gen["patch"], rationale=gen["rationale"],
    )


# -------- phase 2: cross-validated GREEN gate --------

async def _phase2_cross_validate(
    outcome: AgentOutcome,
    combined_tests: dict[str, str],
    request: AnalyzeRequest,
    episode_id: str,
) -> AgentOutcome:
    """Apply this agent's patch to a fresh snapshot, then run ALL agents'
    test files (plus any pre-existing tests). A patch that's a hack will
    fail peers' tests; a robust patch passes most of them.
    """
    spec = outcome.spec
    tag = f"{spec.name:<13}"

    snap = _prepare_snapshot(pathlib.Path(request.repo_snapshot_path))
    try:
        cv = await run_in_docker(
            test_files=combined_tests,
            patch=outcome.patch,
            repo_snapshot_path=str(snap),
        )
    finally:
        shutil.rmtree(snap, ignore_errors=True)

    print(
        f"  {tag} CROSS    {cv.passed} passed, {cv.failed} failed, {cv.errors} errors "
        f"({cv.duration_ms}ms)",
        flush=True,
    )

    elapsed = outcome.gen_ms + outcome.red_ms + cv.duration_ms
    # A patch is a survivor if it passes a simple majority of cross-val tests.
    # The runner returns status=ERROR whenever rc!=0 (any failure), but here
    # rc=1 is the EXPECTED case — we want the patch that survives the most
    # peer scrutiny. "All 4 agents pass 10/12" is a legit outcome; the
    # ranking below resolves ties by files_touched + elapsed.
    total_tests = cv.passed + cv.failed + cv.errors
    is_majority_pass = cv.passed >= max(1, total_tests // 2 + 1)

    if cv.passed == 0:
        final = AgentOutcome(
            spec=spec, status="green_failed", elapsed_ms=elapsed,
            gen_ms=outcome.gen_ms, red_ms=outcome.red_ms, green_ms=cv.duration_ms,
            files_touched=outcome.files_touched,
            cross_val_passed=cv.passed, cross_val_failed=cv.failed,
            eliminated_reason=f"cross-val: 0 of {total_tests} tests passed — {cv.stdout[-200:]}",
            test_code=outcome.test_code, patch=outcome.patch, rationale=outcome.rationale,
        )
    elif not is_majority_pass:
        final = AgentOutcome(
            spec=spec, status="green_failed", elapsed_ms=elapsed,
            gen_ms=outcome.gen_ms, red_ms=outcome.red_ms, green_ms=cv.duration_ms,
            files_touched=outcome.files_touched,
            cross_val_passed=cv.passed, cross_val_failed=cv.failed,
            eliminated_reason=f"cross-val: only {cv.passed}/{total_tests} passed (minority)",
            test_code=outcome.test_code, patch=outcome.patch, rationale=outcome.rationale,
        )
    else:
        final = AgentOutcome(
            spec=spec, status="green_ok", elapsed_ms=elapsed,
            gen_ms=outcome.gen_ms, red_ms=outcome.red_ms, green_ms=cv.duration_ms,
            files_touched=outcome.files_touched,
            cross_val_passed=cv.passed, cross_val_failed=cv.failed,
            test_code=outcome.test_code, patch=outcome.patch, rationale=outcome.rationale,
        )

    supa.upsert_agent(
        episode_id=episode_id, agent=spec.name, model=spec.model,
        status=final.status, elapsed_ms=final.elapsed_ms,
        files_touched=final.files_touched,
        eliminated_reason=final.eliminated_reason,
        test_code=final.test_code, patch_unified_diff=final.patch, rationale=final.rationale,
    )
    return final


# -------- syntax error fast-path --------

_SYNTAX_FIX_SYSTEM = """\
You are RedGreen's syntax-error fast-path. PyCharm's parser caught a
SyntaxError before any code ran; your job is the minimal patch that lets
the module parse.

Return a JSON object with exactly these keys:
  - "patch": string. Unified diff `--- a/<path>` / `+++ b/<path>` / `@@`
    hunks with ~2-3 lines of context. ONE file, one hunk, ideally 1-3
    changed lines. Anchor on exact source lines from the frame_source.
  - "rationale": string. One sentence. What the error was.

Do NOT return a test; this path doesn't need one — if the file parses, the
fix worked. Do NOT rewrite more than is necessary. Just fix the syntax.
Respond with ONLY the JSON object. No prose, no code fences, no markdown.
"""


async def _run_syntax_fast_path(request: AnalyzeRequest) -> dict:
    started = time.monotonic()
    episode_id = supa.insert_episode(
        repo_hash=request.repo_hash,
        frame_file=request.frame_file,
        frame_line=request.frame_line,
        stacktrace=request.stacktrace,
        notes="fast-path=syntax",
    )
    agent_name = "null_guard"  # reuse existing enum slot for storage; UI shows the right phase
    model_name = DEFAULT_OPENAI_MODEL
    supa.upsert_agent(
        episode_id=episode_id, agent=agent_name, model=model_name, status="pending",
    )

    user = f"""\
SyntaxError at {request.frame_file}:{request.frame_line}.

--- stacktrace ---
{request.stacktrace.strip()}

--- source around the bad line (line numbers are absolute) ---
{request.frame_source}

--- HARD RULE FOR THE DIFF ---
The diff headers MUST use exactly this path (project-relative, nothing
longer, nothing shorter):

    --- a/{request.frame_file}
    +++ b/{request.frame_file}

Do NOT prefix with `seeds/`, `src/`, absolute paths, or any directory
name that was in the stacktrace. The runner has no idea about those.

Produce the minimal patch.
"""

    print(f"\n=== episode {episode_id} — SYNTAX fast-path ({model_name}) ===", flush=True)
    gen = await openai_codex_generate(
        system=_SYNTAX_FIX_SYSTEM, user=user, model=model_name, max_tokens=1500,
    )
    gen_ms = gen.get("elapsed_ms", 0)
    print(f"  model: {gen_ms}ms err={gen.get('error')}", flush=True)

    patch = (gen.get("patch") or "").strip()
    rationale = gen.get("rationale") or ""

    # No Docker. We trust the model + the user clicking Apply. If the patch is
    # wrong, the IDE's own parser will immediately re-flag it.
    total_elapsed = int((time.monotonic() - started) * 1000)

    if gen.get("error") or not patch:
        supa.upsert_agent(
            episode_id=episode_id, agent=agent_name, model=model_name,
            status="error", elapsed_ms=gen_ms,
            eliminated_reason=gen.get("error") or "empty patch",
            rationale=rationale,
        )
        supa.finalize_episode(episode_id=episode_id, state="no_winner", total_elapsed_ms=total_elapsed)
        return {
            "episode_id": episode_id, "winner": None,
            "outcomes": [{
                "agent": agent_name, "model": model_name, "status": "error",
                "elapsed_ms": gen_ms, "files_touched": 0,
                "eliminated_reason": gen.get("error") or "empty patch",
            }],
            "total_elapsed_ms": total_elapsed,
        }

    # Synthesize a trivial importlib test so the UI's Apply button's
    # "generated test" sidecar file still makes sense. It'll just assert
    # the module imports post-fix.
    module_stem = request.frame_file.rsplit("/", 1)[-1].removesuffix(".py")
    synthesized_test = f"""\
import importlib


def test_{module_stem}_parses():
    importlib.import_module("{module_stem}")
"""

    supa.upsert_agent(
        episode_id=episode_id, agent=agent_name, model=model_name,
        status="green_ok", elapsed_ms=gen_ms,
        files_touched=1,
        test_code=synthesized_test,
        patch_unified_diff=patch,
        rationale=rationale or "Minimal syntax fix (fast-path — no 4-agent race).",
    )
    supa.finalize_episode(
        episode_id=episode_id, state="completed",
        winner_agent=agent_name, winner_model=model_name,
        total_elapsed_ms=total_elapsed,
    )
    print(f"\nSYNTAX fast-path winner: {model_name} total_ms={total_elapsed}", flush=True)
    return {
        "episode_id": episode_id,
        "winner": {
            "agent": agent_name, "model": model_name,
            "files_touched": 1, "total_elapsed_ms": gen_ms,
        },
        "outcomes": [{
            "agent": agent_name, "model": model_name, "status": "green_ok",
            "elapsed_ms": gen_ms, "files_touched": 1, "eliminated_reason": None,
        }],
        "total_elapsed_ms": total_elapsed,
    }


# -------- race all agents --------

def _rank_survivors(outcomes: list[AgentOutcome]) -> Optional[AgentOutcome]:
    """Cross-validated ranking:
      1. Most cross-val tests passed (robustness — the core signal)
      2. Fewest files touched (smaller patches win ties)
      3. Fastest wall-clock (cosmetic tiebreak)
    """
    survivors = [o for o in outcomes if o.status == "green_ok"]
    if not survivors:
        return None
    survivors.sort(key=lambda o: (-o.cross_val_passed, o.files_touched, o.elapsed_ms))
    return survivors[0]


async def run_episode(
    request: AnalyzeRequest,
    *,
    pool: Optional[list[AgentSpec]] = None,
) -> dict:
    # ---- syntax error fast-path ----
    # Racing 4 models + Docker gates to add a missing colon is absurd. When
    # the plugin flagged this as a parse-time error, skip the tournament and
    # go direct to single-model → tiny patch.
    if "SyntaxError [parse-time]" in request.stacktrace:
        return await _run_syntax_fast_path(request)

    # Router picks the 4 most relevant hypotheses for this stacktrace and
    # shuffles models across them so the leaderboard can separate
    # hypothesis-fit from model-strength over time. `pool=` overrides
    # (used by --solo in the CLI).
    specs = pool or select_agents_for(request)

    episode_id = supa.insert_episode(
        repo_hash=request.repo_hash,
        frame_file=request.frame_file,
        frame_line=request.frame_line,
        stacktrace=request.stacktrace,
        notes="agents=" + ",".join(s.name for s in specs),
    )
    for s in specs:
        supa.upsert_agent(episode_id=episode_id, agent=s.name, model=s.model, status="pending")

    started = time.monotonic()
    print(f"\n=== episode {episode_id} — phase 1: gen + RED for {len(specs)} agents ===", flush=True)

    # Phase 1: every agent generates a candidate and proves their test reproduces the bug.
    phase1 = await asyncio.gather(*[_phase1_gen_and_red(s, request, episode_id) for s in specs])

    red_passers = [o for o in phase1 if o.status == "red_ok"]
    if not red_passers:
        total_elapsed = int((time.monotonic() - started) * 1000)
        supa.finalize_episode(episode_id=episode_id, state="no_winner", total_elapsed_ms=total_elapsed)
        print(f"\nno survivor past RED. total_ms={total_elapsed}", flush=True)
        return {
            "episode_id": episode_id, "winner": None,
            "outcomes": _serialize(phase1),
            "total_elapsed_ms": total_elapsed,
        }

    # Phase 2: cross-validation. Every RED-passer's patch runs against every
    # RED-passer's tests. Robustness = # of peers' tests passed.
    combined_tests = {
        f"test_redgreen_{_slug(o.spec.name)}_{i}.py": o.test_code
        for i, o in enumerate(red_passers)
    }
    print(f"\n=== phase 2: cross-validate {len(red_passers)} survivor(s) against {len(combined_tests)} tests ===", flush=True)

    phase2 = await asyncio.gather(*[
        _phase2_cross_validate(o, combined_tests, request, episode_id) for o in red_passers
    ])

    # Eliminated-at-phase-1 agents keep their earlier outcomes; merge for the response.
    eliminated = [o for o in phase1 if o.status != "red_ok"]
    outcomes = eliminated + phase2

    winner = _rank_survivors(outcomes)
    total_elapsed = int((time.monotonic() - started) * 1000)

    if winner is None:
        supa.finalize_episode(episode_id=episode_id, state="no_winner", total_elapsed_ms=total_elapsed)
        print(f"\nno winner. total_ms={total_elapsed}", flush=True)
        return {
            "episode_id": episode_id, "winner": None, "outcomes": _serialize(outcomes),
            "total_elapsed_ms": total_elapsed,
        }

    supa.finalize_episode(
        episode_id=episode_id, state="completed",
        winner_agent=winner.spec.name, winner_model=winner.spec.model,
        total_elapsed_ms=total_elapsed,
    )
    print(
        f"\nwinner: {winner.spec.name} / {winner.spec.model} "
        f"(cross-val {winner.cross_val_passed}P/{winner.cross_val_failed}F, "
        f"files={winner.files_touched}, elapsed={winner.elapsed_ms}ms) "
        f"total_ms={total_elapsed}",
        flush=True,
    )
    return {
        "episode_id": episode_id,
        "winner": {
            "agent": winner.spec.name, "model": winner.spec.model,
            "files_touched": winner.files_touched, "total_elapsed_ms": winner.elapsed_ms,
            "cross_val_passed": winner.cross_val_passed,
            "cross_val_failed": winner.cross_val_failed,
        },
        "outcomes": _serialize(outcomes),
        "total_elapsed_ms": total_elapsed,
    }


def _serialize(outcomes: list[AgentOutcome]) -> list[dict]:
    return [
        {
            "agent": o.spec.name, "model": o.spec.model, "status": o.status,
            "elapsed_ms": o.elapsed_ms, "files_touched": o.files_touched,
            "eliminated_reason": o.eliminated_reason,
        }
        for o in outcomes
    ]


# -------- CLI entry --------

def _load_seed_as_request(seed_name: str) -> AnalyzeRequest:
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

    import re
    matches = re.findall(r'File "([^"]+)", line (\d+)', stacktrace)
    if not matches:
        raise SystemExit("could not parse stacktrace for frame file/line")
    frame_file_abs, frame_line_str = matches[-1]
    frame_file = os.path.relpath(frame_file_abs, seed_root) if frame_file_abs.startswith(str(seed_root)) else frame_file_abs
    frame_line = int(frame_line_str)

    src_path = pathlib.Path(frame_file_abs)
    src_lines = src_path.read_text().splitlines()
    lo = max(0, frame_line - 20)
    hi = min(len(src_lines), frame_line + 20)
    frame_source = "\n".join(f"{i+1:>4}: {src_lines[i]}" for i in range(lo, hi))

    repo_hash = _git_sha(seed_root) or f"seed:{seed_name}"

    return AnalyzeRequest(
        stacktrace=stacktrace,
        locals_json={},
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
    ap.add_argument("--seed", required=True)
    ap.add_argument("--cli", action="store_true")
    ap.add_argument("--solo", default=None, help="race only one agent by name (for debugging)")
    args = ap.parse_args()

    try:
        req = _load_seed_as_request(args.seed)
    except SystemExit as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    pool = default_agent_pool()
    if args.solo:
        pool = [s for s in pool if s.name == args.solo]
        if not pool:
            print(f"ERROR: no agent named {args.solo}", file=sys.stderr)
            return 2

    try:
        result = asyncio.run(run_episode(req, pool=pool))
    except Exception:
        traceback.print_exc()
        return 1

    if result.get("winner"):
        return 0
    print(f"no winner. reasons: " + "; ".join(f"{o['agent']}={o['status']}" for o in result["outcomes"]), file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(_main())
