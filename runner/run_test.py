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
  - No patch                         -> RED gate: expect pytest to FAIL on the new test.
  - Patch + test_code/test_files     -> GREEN / cross-val gate: return raw pass
                                        counts so the caller can rank survivors.
  - Patch, no tests injected         -> REGRESSION gate: run only the seed's own
                                        `tests/`; rc=1 is a legit "patch broke
                                        something" signal, not a fatal error.
"""

from __future__ import annotations

import json
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


def _parse_pytest_counts(output: str) -> tuple[int, int, int]:
    """Pull (passed, failed, errors) from a pytest summary line.

    Works with both `-q` ("3 passed, 1 failed in 0.05s") and verbose outputs.
    Missing numbers default to 0. Handles the summary appearing anywhere in
    the last few hundred chars.
    """
    passed = failed = errors = 0
    tail = output[-800:]
    m = re.search(r"(\d+)\s+passed", tail)
    if m: passed = int(m.group(1))
    m = re.search(r"(\d+)\s+failed", tail)
    if m: failed = int(m.group(1))
    m = re.search(r"(\d+)\s+error", tail)
    if m: errors = int(m.group(1))
    return passed, failed, errors


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

    tests_dir = repo_root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    # Write test files. Two modes:
    #   - legacy: `test_code` (single file) -> tests/test_redgreen_generated.py
    #   - cross-val: `test_files` (dict name -> content) -> tests/<name>
    #   Both can be present (rare, but supported — write both).
    if req.get("test_files"):
        for name, content in req["test_files"].items():
            safe = pathlib.Path(name).name  # disallow path traversal
            (tests_dir / safe).write_text(content)
    if req.get("test_code"):
        (tests_dir / NEW_TEST_PATH_RELATIVE.split("/", 1)[1]).write_text(req["test_code"])

    patch = req.get("patch_unified_diff")
    if patch:
        try:
            _apply_unified_diff(repo_root, patch)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"status": "ERROR", "stdout": f"patch apply failed: {e}", "duration_ms": 0}))
            return 0

    rc, output, elapsed_ms = _run_pytest(repo_root, "tests/")
    passed, failed, errors = _parse_pytest_counts(output)

    tests_injected = bool(req.get("test_code") or req.get("test_files"))

    if patch is None:
        # RED gate semantics unchanged.
        if rc == 1 and "test_redgreen_generated" in output:
            status = "RED"
        elif rc == 2 and "test_redgreen_generated" in output:
            status = "RED"
        elif rc == 0:
            status = "ERROR"
        else:
            status = "ERROR"
    elif tests_injected:
        # GREEN / cross-val gate. Caller ranks by raw pass count; collapse
        # anything non-zero to ERROR only when pytest itself couldn't run.
        status = "GREEN" if rc in (0, 5) else "ERROR"
    else:
        # REGRESSION gate. Patch + only the seed's existing tests. rc=1 here
        # means the patch broke a pre-existing test — that's the signal we
        # need, not an error condition.
        if rc in (0, 5):
            status = "GREEN"
        elif rc == 1:
            status = "REGRESSION_FAILED"
        else:
            status = "ERROR"

    print(json.dumps({
        "status": status,
        "stdout": output[-4000:],
        "duration_ms": elapsed_ms,
        "passed": passed,
        "failed": failed,
        "errors": errors,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
