"""Happy-path tests — only exercise WARMED buckets so they pass on both
buggy and fixed code. The real bug is on first-call (unwarmed), not here."""

import time

from limiter.token_bucket import TokenBucket


def _warm(bucket: TokenBucket) -> None:
    """Force last_refill to be set without going through the buggy first call.
    Gives us a bucket in a steady state for the happy-path assertions."""
    bucket.last_refill = time.monotonic()
    bucket.tokens = bucket.capacity


def test_warmed_bucket_consume_succeeds():
    b = TokenBucket(capacity=10, refill_per_second=5.0)
    _warm(b)
    assert b.consume(1) is True


def test_warmed_bucket_rejects_over_capacity():
    b = TokenBucket(capacity=3, refill_per_second=1.0)
    _warm(b)
    b.consume(3)
    assert b.consume(1) is False
