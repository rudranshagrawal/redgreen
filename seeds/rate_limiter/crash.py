"""Reproduce the token-bucket cold-start bug.

Fresh bucket → first consume → `now - self.last_refill` raises TypeError
because last_refill is still None.
"""

from src.limiter.token_bucket import TokenBucket


def main() -> None:
    bucket = TokenBucket(capacity=10, refill_per_second=1.0)
    ok = bucket.consume(1)
    print("consumed:", ok)


if __name__ == "__main__":
    main()
