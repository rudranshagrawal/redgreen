"""Quality judge for RedGreen.

After cross-validation has scored each patch by peer-test pass rate, the
judge takes the top candidates and ranks them by *idiomaticness* — does
the patch address the cause, or just silence the symptom?

This replaces the whack-a-mole rule catalog approach: instead of
enumerating specific forbidden anti-patterns (hard-coded "don't change
literals", "don't swallow exceptions", "don't rename to dodge shadows"
etc.) we let a small LLM call assess each patch using the general
code-review principles it already knows.

Architecture: one extra API call per episode, ~500 output tokens, ~$0.001.
Runs AFTER Phase 2 (cross-val) and BEFORE the final winner selection.
Judge's pick is authoritative; cross-val score is the tiebreak.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from openai import AsyncOpenAI

from contracts.schemas import AnalyzeRequest


_JUDGE_SYSTEM = """\
You are the code-review judge for the RedGreen bug-fix tournament.

Multiple patches passed runner validation (each reproduces the bug on the
original code and fixes it after application). Your job is to pick the one
a senior engineer would actually merge — the fix that addresses the CAUSE
rather than silencing the SYMPTOM.

Ranking principles, in priority order:

1. Cause, not symptom. Patches that ADD code (guard clause, domain
   exception, boundary coercion, missing await) beat patches that MUTATE
   existing code values to dodge the failure path. Example: a patch that
   changes `denominator = 0` to `denominator = 1` is a hack — the crash
   goes away but the function now returns a made-up number with no
   semantic grounding. The right fix is a guard that raises when
   denominator would be zero.

2. Match the codebase. If the CODEBASE CONVENTIONS block lists project-
   specific exception classes, prefer those over generic ValueError /
   RuntimeError. Match imports, logging idiom, docstring style.

3. Smallest cause-addressing change. A one-line guard beats a rewrite.
   But a one-line literal-swap "fix" is still a hack — size doesn't
   redeem correctness.

4. Domain-aware assertions. A patch paired with a test that asserts
   real expected behavior (not just current buggy output) gets credit.

You will see: the bug, the source context, each candidate's hypothesis
lens + model + patch + rationale + cross-val score (how many peer tests
they passed).

Return JSON with exactly these keys:
  - "winner_agent": the agent name string of the chosen candidate.
    MUST be one of the agent names shown in the candidates.
  - "reasoning": one short paragraph explaining why the winner is
    best and why the runners-up were rejected. Reference specific
    patch contents.

Respond with ONLY the JSON object. No markdown fences, no prose.
"""


@dataclass
class JudgeVerdict:
    winner_agent: Optional[str]
    reasoning: str
    elapsed_ms: int
    error: Optional[str] = None


_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def _repair_json(raw: str) -> dict[str, Any]:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, flags=re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


async def rank_survivors(
    request: AnalyzeRequest,
    candidates: list[dict],
    *,
    model: str = "gpt-5-mini",
    max_candidates: int = 3,
) -> JudgeVerdict:
    """Judge top candidates. Returns winner agent name + reasoning.

    candidates: list of dicts, each with keys:
      agent, model, rationale, patch, cross_val_passed, cross_val_failed

    Only the top `max_candidates` by cross_val_passed are shown to the
    judge (keeps the prompt bounded).

    If the judge errors or returns something unparseable, winner_agent
    is None — the caller should fall back to the cross-val ranking.
    """
    if not candidates:
        return JudgeVerdict(winner_agent=None, reasoning="no candidates", elapsed_ms=0)
    if len(candidates) == 1:
        return JudgeVerdict(
            winner_agent=candidates[0]["agent"],
            reasoning="only one candidate survived cross-validation",
            elapsed_ms=0,
        )

    sorted_cands = sorted(
        candidates,
        key=lambda c: (-c.get("cross_val_passed", 0), c.get("files_touched", 1)),
    )[:max_candidates]

    user = _build_user_prompt(request, sorted_cands)
    started = time.monotonic()

    try:
        resp = await asyncio.wait_for(
            _get_client().chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _JUDGE_SYSTEM},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=4000,
            ),
            timeout=45.0,
        )
    except asyncio.TimeoutError:
        return JudgeVerdict(
            winner_agent=None,
            reasoning="judge timeout",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error="timeout",
        )
    except Exception as e:  # noqa: BLE001
        return JudgeVerdict(
            winner_agent=None,
            reasoning=f"judge error: {e}",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error=str(e),
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    content = resp.choices[0].message.content or ""
    try:
        obj = _repair_json(content)
    except Exception as e:  # noqa: BLE001
        return JudgeVerdict(
            winner_agent=None,
            reasoning=f"judge returned unparseable JSON: {content[:200]}",
            elapsed_ms=elapsed_ms,
            error=f"bad_json: {e}",
        )

    winner = obj.get("winner_agent")
    reasoning = (obj.get("reasoning") or "").strip()

    valid_agents = {c["agent"] for c in candidates}
    if winner not in valid_agents:
        return JudgeVerdict(
            winner_agent=None,
            reasoning=f"judge named an unknown agent: {winner!r}",
            elapsed_ms=elapsed_ms,
            error="unknown_agent",
        )

    return JudgeVerdict(winner_agent=winner, reasoning=reasoning, elapsed_ms=elapsed_ms)


def _build_user_prompt(request: AnalyzeRequest, candidates: list[dict]) -> str:
    frame_src = request.frame_source[:1500]
    codebase = (request.codebase_context or "").strip()
    codebase_block = (
        f"\n--- CODEBASE CONVENTIONS ---\n{codebase[:1500]}\n"
        if codebase
        else ""
    )

    lines: list[str] = [
        "The bug was caught by the debugger. Survivors of the cross-validated",
        "tournament are below — each passed at least a majority of peer tests.",
        "Pick the one most likely to be merged on code review.",
        "",
        "--- BUG ---",
        f"file: {request.frame_file}:{request.frame_line}",
        "",
        "--- STACKTRACE (tail) ---",
        request.stacktrace.strip()[-1200:],
        "",
        "--- SOURCE AROUND FAILURE ---",
        frame_src,
        codebase_block,
        "--- CANDIDATES ---",
    ]

    for i, c in enumerate(candidates):
        patch = (c.get("patch") or "").strip()[:1800]
        rationale = (c.get("rationale") or "").strip()[:600]
        lines += [
            "",
            f"Candidate [{c['agent']}]",
            f"  model: {c.get('model', '?')}",
            f"  cross-val: {c.get('cross_val_passed', 0)} passed, {c.get('cross_val_failed', 0)} failed",
            f"  files touched: {c.get('files_touched', 1)}",
            "  rationale: " + rationale,
            "  patch:",
            "    " + patch.replace("\n", "\n    "),
        ]

    lines += ["", "Return JSON: winner_agent + reasoning."]
    return "\n".join(lines)
