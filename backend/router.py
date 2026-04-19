"""Stacktrace → hypothesis router.

Selects up to 4 lenses from the 12-entry catalog based on:
  1. Exception type seen in the stacktrace (highest-signal, strongest weight).
  2. Keyword scan over frame_source (weak priors, refine the ranking).

Pure function, no models, no I/O. Runs in milliseconds before the race starts.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from contracts.schemas import Agent


ALL_HYPOTHESES: tuple[Agent, ...] = (
    "null_guard", "input_shape", "async_race", "config_drift",
    "math_error", "resource_leak", "encoding", "recursion",
    "api_contract", "timezone", "auth_permission", "dependency_missing",
)


# Exception type → hypothesis priors. Missing types fall back to a broad default.
# Weights are additive; higher → more relevant.
_EXC_WEIGHTS: dict[str, dict[Agent, int]] = {
    # Type / None
    "TypeError": {"null_guard": 2, "input_shape": 2, "api_contract": 1},
    "AttributeError": {"input_shape": 3, "null_guard": 1, "api_contract": 1},
    "NameError": {"null_guard": 1, "dependency_missing": 2, "config_drift": 1},

    # Shape / lookup
    "KeyError": {"input_shape": 3, "null_guard": 1, "config_drift": 1},
    "IndexError": {"input_shape": 2, "null_guard": 1},
    "ValueError": {"input_shape": 2, "config_drift": 1, "encoding": 1},

    # Math
    "ZeroDivisionError": {"math_error": 4, "null_guard": 1, "input_shape": 1},
    "OverflowError": {"math_error": 3, "input_shape": 1},
    "FloatingPointError": {"math_error": 3},
    "ArithmeticError": {"math_error": 3},

    # Resources
    "FileNotFoundError": {"resource_leak": 2, "config_drift": 2, "input_shape": 1},
    "PermissionError": {"auth_permission": 2, "resource_leak": 1, "config_drift": 1},
    "IsADirectoryError": {"resource_leak": 2, "input_shape": 1},
    "NotADirectoryError": {"resource_leak": 2, "input_shape": 1},
    "OSError": {"resource_leak": 2, "config_drift": 1},
    "IOError": {"resource_leak": 2, "config_drift": 1},
    "BrokenPipeError": {"resource_leak": 2, "async_race": 1},
    "ConnectionError": {"resource_leak": 1, "config_drift": 2, "auth_permission": 1},
    "ConnectionRefusedError": {"config_drift": 2, "resource_leak": 1},
    "ConnectionResetError": {"resource_leak": 2, "async_race": 1},
    "TimeoutError": {"resource_leak": 2, "config_drift": 1, "async_race": 1},

    # Encoding
    "UnicodeDecodeError": {"encoding": 4, "input_shape": 1},
    "UnicodeEncodeError": {"encoding": 4, "input_shape": 1},
    "UnicodeError": {"encoding": 4},
    "LookupError": {"encoding": 2, "input_shape": 1},

    # Recursion
    "RecursionError": {"recursion": 4, "input_shape": 1},

    # Imports
    "ImportError": {"dependency_missing": 4, "api_contract": 1},
    "ModuleNotFoundError": {"dependency_missing": 4},

    # Async
    "CancelledError": {"async_race": 3, "resource_leak": 1},
    "InvalidStateError": {"async_race": 3},

    # Assertions (test-ish)
    "AssertionError": {"input_shape": 1, "null_guard": 1},
    "StopIteration": {"async_race": 2, "input_shape": 1},
    "RuntimeError": {"async_race": 1, "config_drift": 1, "input_shape": 1},

    # Auth / HTTP-ish (observed via strings, not always real exception types)
    "HTTPError": {"api_contract": 2, "auth_permission": 1},
    "ClientError": {"api_contract": 2, "auth_permission": 1},
    "Unauthorized": {"auth_permission": 3},
    "Forbidden": {"auth_permission": 3},
}


# Keyword heuristics on frame_source. Small weights — refinements, not verdicts.
_KEYWORD_WEIGHTS: list[tuple[re.Pattern[str], dict[Agent, int]]] = [
    (re.compile(r"\b(async|await|asyncio\.|asyncio$)"), {"async_race": 1}),
    (re.compile(r"\b(threading|Lock|RLock|Semaphore|Queue)\b"), {"async_race": 1}),
    (re.compile(r"\b(os\.environ|os\.getenv|getenv\()"), {"config_drift": 1}),
    (re.compile(r"\b(open\(|\.close\(\))"), {"resource_leak": 1}),
    (re.compile(r"\bwith\s+[a-zA-Z_][a-zA-Z0-9_]*\("), {"resource_leak": -1}),  # likely fine
    (re.compile(r"\b(datetime|timezone|tzinfo|utcnow|astimezone)"), {"timezone": 1}),
    (re.compile(r"\b(requests\.|httpx\.|urllib|/api/)"), {"api_contract": 1}),
    # Strong auth signals — standalone words, not substrings of data-structure names
    # like `TokenBucket` or `token_bucket.py`. Worth +2 because real auth crashes
    # get swamped otherwise by bystander keywords (decode on jwt.decode, os.environ).
    (re.compile(r"\b(jwt|oauth|bearer)\b", re.IGNORECASE), {"auth_permission": 2}),
    (re.compile(r"\b(permission|forbidden|unauthori[sz]ed|role)\b", re.IGNORECASE), {"auth_permission": 1}),
    (re.compile(r"\b(decode|encode|bytes\(|utf-?8|latin-?1|ascii)", re.IGNORECASE), {"encoding": 1}),
    (re.compile(r"\b(import\s+[a-zA-Z_][a-zA-Z0-9_]*|from\s+[a-zA-Z_])"), {"dependency_missing": 0}),  # informational
    (re.compile(r"\bis\s+None\b|\bNone\s+", re.IGNORECASE), {"null_guard": 1}),
    # Dropped `/\s*[a-zA-Z]` — matched every Unix path in every stacktrace,
    # not an arithmetic signal. Keep only real numeric constructors.
    (re.compile(r"\b(Decimal|Fraction)\(|float\(|int\("), {"math_error": 1}),
    (re.compile(r"\brecursion\b|\bself\.[a-zA-Z_]+\(.*self", re.IGNORECASE), {"recursion": 1}),
]


def _extract_exception_type(stacktrace: str) -> str | None:
    """Pull the `XxxError:` class name from the last line of a Python traceback.

    Handles both bare (`ValueError:`) and dotted (`jwt.exceptions.InvalidTokenError:`)
    forms — real-world tracebacks often include the module path.
    """
    for line in reversed(stacktrace.splitlines()):
        stripped = line.strip()
        # Dotted or bare: pick the last identifier before the colon.
        m = re.match(r"^(?:[a-zA-Z_][a-zA-Z0-9_]*\.)*([A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning|StopIteration)):", stripped)
        if m:
            return m.group(1)
        m2 = re.match(r"^(?:[a-zA-Z_][a-zA-Z0-9_]*\.)*([A-Z][A-Za-z0-9_]*(?:Error|Exception))\s*$", stripped)
        if m2:
            return m2.group(1)
    return None


def score_hypotheses(stacktrace: str, frame_source: str) -> dict[Agent, int]:
    """Return a score per hypothesis (higher = more relevant)."""
    scores: dict[Agent, int] = defaultdict(int)

    exc = _extract_exception_type(stacktrace)
    if exc and exc in _EXC_WEIGHTS:
        for agent, weight in _EXC_WEIGHTS[exc].items():
            scores[agent] += weight
    elif exc:
        # Unknown exception type — fall back to broad defaults.
        scores["null_guard"] += 1
        scores["input_shape"] += 1

    # Keyword priors.
    combined = frame_source + "\n" + stacktrace
    for rx, weights in _KEYWORD_WEIGHTS:
        if rx.search(combined):
            for agent, weight in weights.items():
                scores[agent] += weight

    # Always give every hypothesis a +0.1-ish floor so we can break ties deterministically.
    # We store scores as ints, so use explicit tie-break in pick_top.
    return dict(scores)


def pick_top(
    scores: dict[Agent, int],
    *,
    k: int = 4,
    fallbacks: Iterable[Agent] = ("null_guard", "input_shape", "async_race", "config_drift"),
) -> list[Agent]:
    """Return up to k hypotheses, highest-score-first. Fill with fallbacks if
    the router produced fewer than k candidates."""
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], ALL_HYPOTHESES.index(kv[0])))
    picks = [a for a, s in ranked if s > 0][:k]
    for fb in fallbacks:
        if len(picks) >= k:
            break
        if fb not in picks:
            picks.append(fb)
    return picks[:k]
