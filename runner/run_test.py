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
    """Apply a unified diff using the stdlib — no `patch` binary in the image.

    Supports simple single-file patches of the form emitted by git: `--- a/...`,
    `+++ b/...`, `@@ -a,b +c,d @@` hunks. That is sufficient for RedGreen's
    bounded patches (one function in one file).
    """
    current_file: pathlib.Path | None = None
    hunks: list[tuple[int, list[str]]] = []  # (start_line_in_original, lines)
    active_hunk: list[str] | None = None
    active_start = 0

    def flush_file() -> None:
        nonlocal current_file, hunks
        if current_file is None:
            return
        _apply_hunks_to_file(current_file, hunks)
        current_file = None
        hunks = []

    for line in diff_text.splitlines():
        if line.startswith("--- "):
            flush_file()
        elif line.startswith("+++ "):
            target = line[4:].strip()
            if target.startswith("b/"):
                target = target[2:]
            if target == "/dev/null":
                current_file = None
            else:
                current_file = repo_root / target
        elif line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if not m:
                raise ValueError(f"malformed hunk header: {line!r}")
            active_start = int(m.group(1))
            active_hunk = []
            hunks.append((active_start, active_hunk))
        elif active_hunk is not None and (line.startswith("+") or line.startswith("-") or line.startswith(" ")):
            active_hunk.append(line)

    flush_file()


def _apply_hunks_to_file(path: pathlib.Path, hunks: list[tuple[int, list[str]]]) -> None:
    original = path.read_text().splitlines(keepends=False) if path.exists() else []
    # Apply hunks from bottom to top so earlier line numbers stay valid.
    for start, lines in sorted(hunks, key=lambda h: -h[0]):
        orig_idx = start - 1
        new_block: list[str] = []
        for entry in lines:
            tag, content = entry[0], entry[1:]
            if tag == " ":
                new_block.append(content)
                orig_idx += 1
            elif tag == "-":
                orig_idx += 1
            elif tag == "+":
                new_block.append(content)
        consumed = sum(1 for e in lines if e and e[0] in (" ", "-"))
        original[start - 1:start - 1 + consumed] = new_block
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(original) + ("\n" if original else ""))


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
        # RED gate: we want the new test to FAIL (reproducing the bug).
        # pytest exit code 1 = tests failed, 0 = all passed, others = collection error.
        if rc == 1 and "test_redgreen_generated" in output:
            status = "RED"
        elif rc == 0:
            status = "ERROR"  # test didn't reproduce the bug
        else:
            status = "ERROR"
    else:
        # GREEN gate: everything must pass now.
        status = "GREEN" if rc == 0 else "ERROR"

    print(json.dumps({"status": status, "stdout": output[-4000:], "duration_ms": elapsed_ms}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
