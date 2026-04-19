"""System prompts for each of the 4 hypotheses.

Each hypothesis is an opinionated lens on what kind of bug the model should
look for. Models race to produce `(test_code, patch, rationale)`; the referee
then decides which lenses were actually right on this codebase. Episode 1
knows nothing about which lens to trust — episode 20 does.

Contract with the model:

- `test_code`: a pytest test that fails on the current code and passes after
  the patch is applied. Must import from the repo's modules (not inline).
  DO NOT use `pytest.raises(...)` as the sole assertion — that pattern passes
  on the buggy code and fails after the fix, which is backwards.
- `patch`: unified diff (`--- a/PATH` / `+++ b/PATH` / `@@` hunks) anchored
  on the file path relative to the repo root.
- `rationale`: one paragraph, plain English, why this is the bug.
"""

from __future__ import annotations

from typing import Literal

Hypothesis = Literal["null_guard", "input_shape", "async_race", "config_drift"]


_OUTPUT_CONTRACT = """\
Return a single JSON object with exactly these keys:
  - "test_code": string. A complete pytest test module. Must fail on the
    current code (because the bug is present) and pass after `patch` is applied.
    Import the function(s) under test from the repo's package — do not inline
    the implementation. Do not use `pytest.raises` as the sole assertion.
  - "patch": string. A unified diff starting with `--- a/<path>` and `+++ b/<path>`,
    paths relative to the repo root. One file only. Keep the change minimal —
    ideally 1-5 lines.
  - "rationale": string. One paragraph. Plain English. Why this is the bug,
    under the hypothesis lens you were given.

Respond with ONLY the JSON object. No prose, no code fences, no markdown.
"""


NULL_GUARD = f"""\
You are the `null_guard` agent in a racing tournament of bug-fixing models.

Your lens: the failing code is crashing because something that can legitimately
be None (or empty, or unset) is being used as if it were always present. The
fix is a guard clause at the right place — an early return, an `or default`,
a domain-specific exception, etc. — that preserves intended behavior for the
normal path.

Do NOT rewrite the function. Add the smallest, clearest guard.

{_OUTPUT_CONTRACT}
"""

INPUT_SHAPE = f"""\
You are the `input_shape` agent.

Your lens: the caller passed data in a shape the callee didn't expect — a
list instead of a tuple, a dict instead of a dataclass, an int instead of a
Decimal, a bytes instead of a str, etc. The fix is either a coercion at the
boundary, a typed parse, or an explicit validation error.

Do NOT invent new validation layers. Coerce or validate at the specific call
site where the shape mismatch enters.

{_OUTPUT_CONTRACT}
"""

ASYNC_RACE = f"""\
You are the `async_race` agent.

Your lens: the failing code depends on ordering or atomicity that isn't
actually enforced — a missing `await`, an `asyncio.gather` losing exceptions,
a shared mutable captured by a closure in a loop, a thread reading a value
before another thread writes it, a cache populated after it's read.

The fix is structural (add the await, use a lock, freeze the closure, reorder
the ops) — never "add a retry" or "add a sleep".

{_OUTPUT_CONTRACT}
"""

CONFIG_DRIFT = f"""\
You are the `config_drift` agent.

Your lens: the code works in one environment and fails in another because an
assumption about config drifted — a wrong env var default, a hard-coded URL,
a port that differs in test, a feature flag that didn't roll out, a secret
that got rotated.

The fix is to make the configuration explicit where it's used, fall back
sensibly, or surface the miss with a clear error — not to paper over the
missing value.

{_OUTPUT_CONTRACT}
"""


_PROMPTS: dict[Hypothesis, str] = {
    "null_guard": NULL_GUARD,
    "input_shape": INPUT_SHAPE,
    "async_race": ASYNC_RACE,
    "config_drift": CONFIG_DRIFT,
}


def system_prompt(hypothesis: Hypothesis) -> str:
    return _PROMPTS[hypothesis]


def user_prompt(
    *,
    stacktrace: str,
    frame_file: str,
    frame_line: int,
    frame_source: str,
    locals_json: dict,
) -> str:
    """Assemble the bounded context we hand every model."""
    import json as _json

    locals_preview = _json.dumps(locals_json, default=str, indent=2)[:2000]
    is_syntax_error = "SyntaxError [parse-time]" in stacktrace

    # ---- import guidance (avoids "No module named 'seeds'" class of errors) ----
    # The runner mounts `repo_snapshot_path` at /work and pytest runs with cwd=/work.
    # Two layouts we support:
    #   (a) flat scripts at repo root (e.g. zero_error.py at project root)
    #       -> import as `from <stem> import X`
    #   (b) src/ layout with pytest.ini pointing pythonpath=src
    #       -> import as `from <pkg>.<module> import X`
    #
    # We build a CONCRETE template string the model can copy verbatim. Past
    # runs showed models ignoring abstract prose ("use this style") but
    # obeying literal templates.
    if "/" in frame_file and frame_file.startswith("src/"):
        module_path = frame_file.removeprefix("src/").removesuffix(".py").replace("/", ".")
        import_template = f"from {module_path} import <symbol>"
    else:
        stem = frame_file.rsplit("/", 1)[-1].removesuffix(".py")
        import_template = f"from {stem} import <symbol>"

    syntax_block = ""
    if is_syntax_error:
        syntax_block = """\

--- THIS IS A PARSE-TIME ERROR ---
PyCharm caught a SyntaxError before any of the module ran. The whole file fails
to parse. That has two consequences:
  1. A naive `from <module> import X` at the top of the test file will die
     during pytest *collection*, which the runner records as ERROR (not RED).
  2. The fix is usually trivial — a missing colon, unclosed paren, bad indent.
Write the test using `importlib.import_module(...)` inside a test function:

    import importlib
    def test_module_parses():
        importlib.import_module("<dotted.module.path>")

The bug makes importlib raise — test fails → RED.
After your patch, importlib succeeds → test passes → GREEN.
"""

    return f"""\
The user hit an exception in their debugger. Here is the failure:

--- stacktrace ---
{stacktrace.strip()}

--- failing frame ---
file: {frame_file}
line: {frame_line}

--- source around the failing line ---
{frame_source}

--- locals at the failing frame (JSON, truncated) ---
{locals_preview}
{syntax_block}
--- HARD RULES FOR test_code (ignore these and you lose) ---

RULE 1 — IMPORT PATH. Your test file MUST import the failing symbol with:

    {import_template}

Do NOT invent package paths. Specifically FORBIDDEN:
  - from null_guard import ...     (that's the project folder name, not a package)
  - from seeds.anything import ...  (no such package)
  - from redgreen import ...        (no such package)
  - import {frame_file.rsplit("/", 1)[-1].removesuffix(".py")} as ...  (use `from ... import`)

RULE 2 — NO MODULE-SCOPE EXECUTION. Every function call, print, or
expression that TRIGGERS the bug must live inside a `def test_*` function.
Module-scope code runs during pytest COLLECTION, which returns rc=2, which
the runner treats as ERROR — you lose automatically.

  WRONG (collection dies):
      from mymod import buggy
      print(buggy(10))          # <-- this runs at collect time, boom
      def test_x(): ...

  RIGHT:
      from mymod import buggy
      def test_x():
          assert buggy(10) == expected   # <-- runs inside pytest

RULE 3 — NO `pytest.raises` AS THE SOLE ASSERTION. The test must FAIL on
the current buggy code and PASS after your patch is applied.
`with pytest.raises(X):` passes on buggy code (the exception IS raised as
expected) and fails after the fix (no exception) — that's backwards.
Use `assert actual == expected` instead.

If your test file uses any pytest helper, start with `import pytest`.

Propose a test + patch per the JSON contract.
"""
