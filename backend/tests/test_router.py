"""Router regression tests.

Hard rule #9: failing test first. These pin the two router bugs the
rate_limiter episode surfaced:

1. `/\\s*[a-zA-Z]` in the math_error keyword list matches `/Users/...`
   in any stacktrace path, biasing math_error on every crash.
2. `token` in the auth_permission keyword list matches `TokenBucket`,
   `token_bucket.py`, `self.tokens` — common data-structure words, not
   auth concepts.

Run: .venv/bin/python -m backend.tests.test_router
"""

from __future__ import annotations

from backend.router import pick_top, score_hypotheses


# Exact stacktrace + frame_source the rate_limiter seed produces on a fresh bucket.
RATE_LIMITER_STACKTRACE = """\
Traceback (most recent call last):
  File "/Users/rudranshagrawal/rudy/coding-projects/redgreen/seeds/rate_limiter/crash.py", line 12, in main
    ok = bucket.consume(1)
  File "/Users/rudranshagrawal/rudy/coding-projects/redgreen/seeds/rate_limiter/src/limiter/token_bucket.py", line 30, in consume
    elapsed = now - self.last_refill
TypeError: unsupported operand type(s) for -: 'float' and 'NoneType'
"""

RATE_LIMITER_FRAME_SOURCE = """\
  10: from typing import Optional
  11:
  12:
  13: @dataclass
  14: class TokenBucket:
  15:     capacity: int
  16:     refill_per_second: float
  17:     tokens: float = 0.0
  18:     last_refill: Optional[float] = None
  19:
  20:     def consume(self, n: int = 1) -> bool:
  21:         \"\"\"Try to take n tokens. Return True on success, False if not enough.\"\"\"
  22:         now = time.monotonic()
  23:         # BUG: on first call, last_refill is None. `now - None` -> TypeError.
  24:         elapsed = now - self.last_refill
  25:         self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
  26:         self.last_refill = now
"""


def test_rate_limiter_does_not_pick_math_error() -> None:
    """TypeError against 'NoneType' is not an arithmetic bug. The path-slash
    regex was giving math_error a free +1 on every stacktrace."""
    scores = score_hypotheses(RATE_LIMITER_STACKTRACE, RATE_LIMITER_FRAME_SOURCE)
    picks = pick_top(scores, k=4)
    assert "math_error" not in picks, f"math_error leaked into top-4: {picks} (scores={scores})"


def test_rate_limiter_does_not_pick_auth_permission() -> None:
    """`TokenBucket` is a data-structure, not an auth primitive. The bare
    `\\btoken` keyword was pulling auth_permission into any code that touches
    rate limits, caches, queues, etc."""
    scores = score_hypotheses(RATE_LIMITER_STACKTRACE, RATE_LIMITER_FRAME_SOURCE)
    picks = pick_top(scores, k=4)
    assert "auth_permission" not in picks, f"auth_permission leaked into top-4: {picks} (scores={scores})"


def test_rate_limiter_picks_null_guard_first() -> None:
    """The 'NoneType' in the exception message is the load-bearing signal.
    Whatever else the router picks, null_guard should be #1."""
    scores = score_hypotheses(RATE_LIMITER_STACKTRACE, RATE_LIMITER_FRAME_SOURCE)
    picks = pick_top(scores, k=4)
    assert picks[0] == "null_guard", f"expected null_guard first, got {picks}"


def test_real_auth_stacktrace_still_picks_auth_permission() -> None:
    """Don't regress the case the `token` keyword was originally added for:
    an OAuth/JWT error should still route to auth_permission."""
    stacktrace = """\
Traceback (most recent call last):
  File "app/api/auth.py", line 42, in verify_token
    payload = jwt.decode(bearer_token, key, algorithms=['HS256'])
jwt.exceptions.InvalidTokenError: Signature verification failed
"""
    frame_source = """\
  40: def verify_token(bearer_token: str) -> dict:
  41:     key = os.environ['JWT_SECRET']
  42:     payload = jwt.decode(bearer_token, key, algorithms=['HS256'])
  43:     return payload
"""
    scores = score_hypotheses(stacktrace, frame_source)
    picks = pick_top(scores, k=4)
    assert "auth_permission" in picks, f"auth keyword regressed; picks={picks} (scores={scores})"


def test_real_division_stacktrace_still_picks_math_error() -> None:
    """Don't regress the real math_error signal — ZeroDivisionError should
    still route to math_error with or without the path-slash keyword."""
    stacktrace = """\
Traceback (most recent call last):
  File "compute.py", line 8, in ratio
    return hits / total
ZeroDivisionError: division by zero
"""
    frame_source = """\
   6: def ratio(hits: int, total: int) -> float:
   7:     # caller promises total > 0, but sometimes doesn't
   8:     return hits / total
"""
    scores = score_hypotheses(stacktrace, frame_source)
    picks = pick_top(scores, k=4)
    assert picks[0] == "math_error", f"expected math_error first, got {picks} (scores={scores})"


TESTS = [
    test_rate_limiter_does_not_pick_math_error,
    test_rate_limiter_does_not_pick_auth_permission,
    test_rate_limiter_picks_null_guard_first,
    test_real_auth_stacktrace_still_picks_auth_permission,
    test_real_division_stacktrace_still_picks_math_error,
]


def _main() -> int:
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failures += 1
    print(f"\n{len(TESTS) - failures}/{len(TESTS)} passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(_main())
