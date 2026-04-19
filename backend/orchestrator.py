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

from backend import hypotheses, judge, router, supa  # noqa: E402
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
    """Route + bias + rotate: pick top-4 hypotheses for this stacktrace,
    then assign models — history-biased when we've seen this codebase
    before, deterministic-shuffle on cold start.

    The feedback loop that makes "episode 20 picks the right model first
    try" actually true:

      1. router.score_hypotheses picks the lens candidates from the
         exception type and frame keywords.
      2. For each picked lens, we check supa.read_winner_history for
         (lens, model) pairs that have won on THIS repo_hash before.
         The winningest-available model gets that lens this episode.
      3. Any lens with no prior win on this repo falls back to the
         seeded random shuffle (so cold starts still look fair).
      4. A model already assigned to a historical winner this episode
         isn't reused for another lens — keeps the race diverse.

    When no history exists (new codebase), behavior is identical to the
    old pure-shuffle version. When history accumulates, the roster
    reflects it — and the plugin's leaderboard preview ("predicts X with
    Y% confidence") finally reflects the runtime choice, not just the
    dashboard's retrospective.
    """
    scores = router.score_hypotheses(request.stacktrace, request.frame_source)
    picks = router.pick_top(scores, k=len(_MODEL_ROSTER))

    # Historical winners for this codebase, if any.
    try:
        history = supa.read_winner_history(request.repo_hash)
    except Exception as e:  # noqa: BLE001
        # Don't let a transient Supabase hiccup block the race.
        print(f"  [history] skipped: {e}", flush=True)
        history = {}

    rng = random.Random(
        seed if seed is not None else hash((request.repo_hash, request.frame_file, request.frame_line))
    )
    shuffled_models = list(_MODEL_ROSTER)
    rng.shuffle(shuffled_models)
    roster_by_model = {m: p for m, p in _MODEL_ROSTER}

    specs: list[AgentSpec] = []
    used_models: set[str] = set()
    bias_notes: list[str] = []

    for agent_name in picks:
        # 1. History lookup: best prior (agent, model) pair still available.
        history_pick: Optional[tuple[str, int]] = None
        for (a, m), count in sorted(history.items(), key=lambda kv: -kv[1]):
            if a == agent_name and m in roster_by_model and m not in used_models:
                history_pick = (m, count)
                break

        if history_pick is not None:
            model_name, wins = history_pick
            specs.append(AgentSpec(agent_name, model_name, roster_by_model[model_name]))
            used_models.add(model_name)
            bias_notes.append(f"{agent_name}←{model_name} ({wins}W)")
            continue

        # 2. Cold-start / no-match-in-history: next model in shuffled order.
        for model_name, provider in shuffled_models:
            if model_name not in used_models:
                specs.append(AgentSpec(agent_name, model_name, provider))
                used_models.add(model_name)
                bias_notes.append(f"{agent_name}←{model_name} (shuffle)")
                break

    if any("W)" in n for n in bias_notes):
        print(f"  [feedback] {' | '.join(bias_notes)}", flush=True)
    return specs


# -------- per-agent outcome --------

@dataclass
class AgentOutcome:
    spec: AgentSpec
    status: str  # pending | red_ok | red_failed | green_ok | green_failed | regression_failed | error
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
    regression_passed: int = 0
    regression_failed: int = 0
    regression_ms: int = 0


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


# -------- phase 2.5: regression gate --------

async def _phase25_regression(
    outcome: AgentOutcome,
    request: AnalyzeRequest,
    episode_id: str,
) -> AgentOutcome:
    """Apply this agent's patch on a fresh snapshot and run ONLY the seed's
    pre-existing test suite — no injected peer tests. Any failure means the
    patch fixed its target bug but broke something else in the codebase; we
    disqualify it (strict), unless *every* survivor fails regression, in
    which case the caller promotes the least-broken one back to green_ok
    (soft fallback).
    """
    spec = outcome.spec
    tag = f"{spec.name:<13}"

    snap = _prepare_snapshot(pathlib.Path(request.repo_snapshot_path))
    try:
        reg = await run_in_docker(
            test_code=None,
            test_files=None,
            patch=outcome.patch,
            repo_snapshot_path=str(snap),
        )
    finally:
        shutil.rmtree(snap, ignore_errors=True)

    total_elapsed = outcome.gen_ms + outcome.red_ms + outcome.green_ms + reg.duration_ms

    # Merge fresh regression data into the existing outcome fields.
    outcome.regression_passed = reg.passed
    outcome.regression_failed = reg.failed + reg.errors
    outcome.regression_ms = reg.duration_ms
    outcome.elapsed_ms = total_elapsed

    print(
        f"  {tag} REGRESS  {reg.passed} passed, {reg.failed} failed, {reg.errors} errors "
        f"({reg.duration_ms}ms, status={reg.status})",
        flush=True,
    )

    if reg.status == "REGRESSION_FAILED" or outcome.regression_failed > 0:
        outcome.status = "regression_failed"
        outcome.eliminated_reason = (
            f"regression: broke {outcome.regression_failed} pre-existing test(s) "
            f"({reg.passed} still pass) — {reg.stdout[-160:]}"
        )
    elif reg.status == "ERROR":
        # Pytest couldn't run cleanly against the patched code — treat as broken too.
        outcome.status = "regression_failed"
        outcome.regression_failed = max(outcome.regression_failed, 1)
        outcome.eliminated_reason = f"regression: pytest error — {reg.stdout[-160:]}"
    # else: status stays "green_ok" — patch didn't break anything.

    supa.upsert_agent(
        episode_id=episode_id, agent=spec.name, model=spec.model,
        status=outcome.status, elapsed_ms=outcome.elapsed_ms,
        files_touched=outcome.files_touched,
        eliminated_reason=outcome.eliminated_reason,
        test_code=outcome.test_code, patch_unified_diff=outcome.patch,
        rationale=outcome.rationale,
        cross_val_passed=outcome.cross_val_passed,
        cross_val_failed=outcome.cross_val_failed,
        regression_passed=outcome.regression_passed,
        regression_failed=outcome.regression_failed,
        regression_ms=outcome.regression_ms,
    )
    return outcome


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

def _rank_survivors(outcomes: list[AgentOutcome]) -> list[AgentOutcome]:
    """Survivor ordering: patches that passed both cross-val and regression,
    sorted by most peer tests passed, most existing tests still passing,
    fewest files touched, fastest wall-clock.
    """
    survivors = [o for o in outcomes if o.status == "green_ok"]
    survivors.sort(key=lambda o: (
        -o.cross_val_passed,
        -o.regression_passed,
        o.files_touched,
        o.elapsed_ms,
    ))
    return survivors


def _soft_fallback(outcomes: list[AgentOutcome]) -> Optional[AgentOutcome]:
    """If every candidate broke something, pick the least-broken one and
    promote it back to green_ok with an explicit note. Never return
    no_winner purely because everyone was imperfect."""
    regressed = [o for o in outcomes if o.status == "regression_failed"]
    if not regressed:
        return None
    # Prefer fewest regression failures, then most cross-val passes, then fewest files.
    regressed.sort(key=lambda o: (
        o.regression_failed,
        -o.cross_val_passed,
        o.files_touched,
        o.elapsed_ms,
    ))
    picked = regressed[0]
    picked.status = "green_ok"
    note = f" (regression fallback · broke {picked.regression_failed} pre-existing test(s))"
    picked.eliminated_reason = (picked.eliminated_reason or "") + note
    print(
        f"  FALLBACK promoted {picked.spec.name} / {picked.spec.model} "
        f"(regression {picked.regression_failed}F, cross-val {picked.cross_val_passed}P) — no clean winners",
        flush=True,
    )
    return picked


async def _judge_and_rank(
    request: AnalyzeRequest, outcomes: list[AgentOutcome],
) -> tuple[Optional[AgentOutcome], Optional[judge.JudgeVerdict]]:
    """Combine cross-val score + quality judge into a final winner.

    The judge picks based on idiomaticness (guard vs hack, domain
    exception vs generic, match codebase conventions). The cross-val
    score is the tiebreak if the judge returns something we can't use.
    """
    ranked = _rank_survivors(outcomes)
    if not ranked:
        return None, None
    if len(ranked) == 1:
        return ranked[0], judge.JudgeVerdict(
            winner_agent=ranked[0].spec.name,
            reasoning="only one survivor; no judging needed.",
            elapsed_ms=0,
        )

    # Ask the judge to re-rank the top survivors.
    candidates = [
        {
            "agent": o.spec.name,
            "model": o.spec.model,
            "rationale": o.rationale,
            "patch": o.patch,
            "cross_val_passed": o.cross_val_passed,
            "cross_val_failed": o.cross_val_failed,
            "regression_passed": o.regression_passed,
            "regression_failed": o.regression_failed,
            "files_touched": o.files_touched,
        }
        for o in ranked[:3]
    ]
    verdict = await judge.rank_survivors(request, candidates)

    if verdict.winner_agent:
        judge_pick = next((o for o in ranked if o.spec.name == verdict.winner_agent), None)
        if judge_pick is not None:
            print(
                f"  JUDGE    picked {judge_pick.spec.name} "
                f"(cross-val {judge_pick.cross_val_passed}P, regression {judge_pick.regression_passed}P, "
                f"judge {verdict.elapsed_ms}ms): {verdict.reasoning[:160]}",
                flush=True,
            )
            return judge_pick, verdict

    # Judge errored or named an agent we don't recognize — fall back to cross-val top.
    print(
        f"  JUDGE    fell back to cross-val ranking ({verdict.error or 'no winner'}): "
        f"{verdict.reasoning[:120]}",
        flush=True,
    )
    return ranked[0], verdict


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

    # Phase 2.5: regression gate. Each cross-val survivor's patch runs against
    # ONLY the seed's pre-existing tests. A clean fix passes; a hack or a
    # patch with a side-effect gets disqualified. Strict-with-soft-fallback:
    # if every survivor fails regression we still pick the least-broken one,
    # so the plugin always has something to apply.
    cv_survivors = [o for o in phase2 if o.status == "green_ok"]
    if cv_survivors:
        print(f"\n=== phase 2.5: regression gate on {len(cv_survivors)} survivor(s) ===", flush=True)
        await asyncio.gather(*[
            _phase25_regression(o, request, episode_id) for o in cv_survivors
        ])

        if all(o.status == "regression_failed" for o in cv_survivors):
            _soft_fallback(cv_survivors)

    # Eliminated-at-phase-1 agents keep their earlier outcomes; merge for the response.
    eliminated = [o for o in phase1 if o.status != "red_ok"]
    outcomes = eliminated + phase2

    # Phase 3: quality judge — picks the most idiomatic fix among top survivors.
    winner, verdict = await _judge_and_rank(request, outcomes)
    total_elapsed = int((time.monotonic() - started) * 1000)

    if winner is None:
        supa.finalize_episode(episode_id=episode_id, state="no_winner", total_elapsed_ms=total_elapsed)
        print(f"\nno winner. total_ms={total_elapsed}", flush=True)
        return {
            "episode_id": episode_id, "winner": None, "outcomes": _serialize(outcomes),
            "total_elapsed_ms": total_elapsed,
        }

    # Persist the judge's reasoning to the winner row so the UI can surface it.
    judge_note = ""
    if verdict is not None and verdict.winner_agent == winner.spec.name and verdict.reasoning:
        judge_note = f"\n\n[Judge] {verdict.reasoning}"
        supa.upsert_agent(
            episode_id=episode_id, agent=winner.spec.name, model=winner.spec.model,
            status="green_ok", elapsed_ms=winner.elapsed_ms,
            files_touched=winner.files_touched,
            test_code=winner.test_code, patch_unified_diff=winner.patch,
            rationale=(winner.rationale or "") + judge_note,
            cross_val_passed=winner.cross_val_passed,
            cross_val_failed=winner.cross_val_failed,
            regression_passed=winner.regression_passed,
            regression_failed=winner.regression_failed,
            regression_ms=winner.regression_ms,
        )

    supa.finalize_episode(
        episode_id=episode_id, state="completed",
        winner_agent=winner.spec.name, winner_model=winner.spec.model,
        total_elapsed_ms=total_elapsed,
    )
    print(
        f"\nwinner: {winner.spec.name} / {winner.spec.model} "
        f"(cross-val {winner.cross_val_passed}P/{winner.cross_val_failed}F, "
        f"regression {winner.regression_passed}P/{winner.regression_failed}F, "
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
            "regression_passed": winner.regression_passed,
            "regression_failed": winner.regression_failed,
            "judge_reasoning": verdict.reasoning if verdict else "",
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
            "cross_val_passed": o.cross_val_passed,
            "cross_val_failed": o.cross_val_failed,
            "regression_passed": o.regression_passed,
            "regression_failed": o.regression_failed,
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
