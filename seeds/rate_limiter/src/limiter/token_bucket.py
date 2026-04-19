"""Token bucket rate limiter.

Bug: `consume` crashes when called on a freshly-constructed bucket because
`last_refill` is None until the first successful call. The refill math
does `now - self.last_refill` which raises TypeError.

A correct fix initializes `last_refill` to the current time at construction
(or guards the None case on first call), so the first `consume` works
and the bucket starts full.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TokenBucket:
    capacity: int
    refill_per_second: float
    tokens: float = 0.0
    last_refill: Optional[float] = None

    def consume(self, n: int = 1) -> bool:
        """Try to take n tokens. Return True on success, False if not enough."""
        now = time.monotonic()
        # BUG: on first call, last_refill is None. `now - None` → TypeError.
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.last_refill = now

        if self.tokens >= n:
            self.tokens -= n
            return True
        return False
