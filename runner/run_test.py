"""Stateless pytest referee for RedGreen.

Runs inside the Docker runner. Expects a JSON RunRequest on stdin:

    {
      "episode_id": "...",
      "agent": "null_guard",
      "test_code": "def test_repro(): ...",
      "patch_unified_diff": "...",   // optional
      "repo_snapshot_path": "/work"  // snapshot mounted at /work
    }

Returns a JSON RunResponse on stdout:

    {"status": "RED"|"GREEN"|"ERROR", "stdout": "...", "duration_ms": int}

Gate semantics:
  - No patch provided -> RED gate: expect pytest to FAIL on the new test.
  - Patch provided    -> GREEN gate: expect pytest to PASS on the new test
                                     AND all pre-existing tests still pass.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import time


NEW_TEST_PATH_RELATIVE = "tests/test_redgreen_generated.py"


def _log(msg: str) -> None:
    sys.stderr.write(msg.rstrip() + "\n")
    sys.stderr.flush()


def _apply_unified_diff(repo_root: pathlib.Path, diff_text: str) -> None:
    """Apply a unified diff using context-based search & replace.

    We intentionally IGNORE the line numbers in `@@` headers — real models
    emit stale, wrong, or entirely missing coordinates. The hunk body itself
    contains enough context: the sequence of ` ` + `-` lines must appear
    verbatim in the target file, and we replace it with the sequence of
    ` ` + `+` lines.

    Supports:
      - `@@` with or without line numbers
      - multiple hunks per file
      - multiple files per diff
      - new-file creation (`--- /dev/null`)
    """
    current_file: pathlib.Path | None = None
    hunks: list[list[str]] = []
    active_hunk: list[str] | None = None
    file_is_new = False

    def flush_file() -> None:
        nonlocal current_file, hunks
        if current_file is None:
            return
        if file_is_new:
            # New-file hunk: just write the "+" lines.
            new_lines = [ln[1:] for h in hunks for ln in h if ln.startswith("+")]
            current_file.parent.mkdir(parents=True, exist_ok=True)
            current_file.write_text("\n".join(new_lines) + ("\n" if new_lines else ""))
        else:
            _apply_hunks_to_file(current_file, hunks)
        current_file = None
        hunks = []

    for line in diff_text.splitlines():
        if line.startswith("--- "):
            flush_file()
            file_is_new = line[4:].strip() in ("/dev/null", "a//dev/null")
        elif line.startswith("+++ "):
            target = line[4:].strip()
            if target.startswith("b/"):
                target = target[2:]
            if target == "/dev/null":
                current_file = None
            else:
                current_file = repo_root / target
        elif line.startswith("@@"):
            active_hunk = []
            hunks.append(active_hunk)
        elif active_hunk is not None and line and line[0] in ("+", "-", " "):
            active_hunk.append(line)
        # Silently ignore other lines (e.g., `diff --git`, `index ...`, blank lines).

    flush_file()


def _apply_hunks_to_file(path: pathlib.Path, hunks: list[list[str]]) -> None:
    if not path.exists():
        raise FileNotFoundError(f"patch targets missing file: {path}")
    original_text = path.read_text()
    original_lines = original_text.splitlines()

    for hunk in hunks:
        if not hunk:
            continue
        before = [ln[1:] for ln in hunk if ln and ln[0] in (" ", "-")]
        after = [ln[1:] for ln in hunk if ln and ln[0] in (" ", "+")]

        if not before:
            # Pure-insertion hunk: unsafe without an anchor. Refuse.
            raise ValueError("pure-insertion hunk without context — cannot locate")

        idx = _find_subsequence(original_lines, before)
        if idx < 0:
            # Try tolerating trailing-whitespace / tab diffs.
            idx = _find_subsequence(
                [s.rstrip() for s in original_lines],
                [s.rstrip() for s in before],
            )
        if idx < 0:
            raise ValueError(f"hunk does not match any location; first before-line: {before[0]!r}")

        original_lines[idx:idx + len(before)] = after

    trailing_nl = "\n" if original_text.endswith("\n") else ""
    path.write_text("\n".join(original_lines) + trailing_nl)


def _find_subsequence(haystack: list[str], needle: list[str]) -> int:
    if not needle or len(needle) > len(haystack):
        return -1
    for i in range(0, len(haystack) - len(needle) + 1):
        if haystack[i:i + len(needle)] == needle:
            return i
    return -1


def _run_pytest(repo_root: pathlib.Path, target: str) -> tuple[int, str, int]:
    started = time.monotonic()
    proc = subprocess.run(
        ["python", "-m", "pytest", target, "-q", "--tb=short", "-p", "no:cacheprovider"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=60,
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    combined = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return proc.returncode, combined, elapsed_ms


def main() -> int:
    raw = sys.stdin.read()
    try:
        req = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "ERROR", "stdout": f"bad json on stdin: {e}", "duration_ms": 0}))
        return 0

    repo_root = pathlib.Path(req["repo_snapshot_path"])
    if not repo_root.exists():
        print(json.dumps({"status": "ERROR", "stdout": f"snapshot missing: {repo_root}", "duration_ms": 0}))
        return 0

    test_path = repo_root / NEW_TEST_PATH_RELATIVE
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(req["test_code"])

    patch = req.get("patch_unified_diff")
    if patch:
        try:
            _apply_unified_diff(repo_root, patch)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"status": "ERROR", "stdout": f"patch apply failed: {e}", "duration_ms": 0}))
            return 0

    # Always run ALL tests so we catch regressions, not just the new one.
    rc, output, elapsed_ms = _run_pytest(repo_root, "tests/")

    if patch is None:
        # RED gate: the test should FAIL on unpatched code (bug reproduces).
        # rc=1 → a test failed (ideal case).
        # rc=2 → collection error (import raised). That's ALSO valid: if the
        #   model wrote a test that triggers the bug at import time, pytest
        #   crashes during collection. The bug still reproduced — count it.
        # rc=0 → all tests passed → model's test didn't hit the bug. Lose.
        if rc == 1 and "test_redgreen_generated" in output:
            status = "RED"
        elif rc == 2 and "test_redgreen_generated" in output:
            # Collection-time crash counts as RED as long as the generated
            # test file is what caused the failure (not an unrelated import
            # on the happy path).
            status = "RED"
        elif rc == 0:
            status = "ERROR"
        else:
            status = "ERROR"
    else:
        # GREEN gate: everything must pass now.
        # Accept rc=0 (normal) and rc=5 (pytest exits 5 when no tests are
        # collected — if the generated test file was import-time-only and
        # now imports cleanly, pytest finds no test functions, which is fine
        # because the fact that the import succeeded IS the proof).
        status = "GREEN" if rc in (0, 5) else "ERROR"

    print(json.dumps({"status": status, "stdout": output[-4000:], "duration_ms": elapsed_ms}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
