# rate_limiter seed

A classic cold-start bug hiding a deeper semantic issue. Designed to stress
the full runner → cross-val → judge pipeline, not just the first layer.

## The crash

`TypeError: unsupported operand type(s) for -: 'float' and 'NoneType'` at
`src/limiter/token_bucket.py::TokenBucket.consume` line 29 (`now - self.last_refill`).
First call to `consume()` on a freshly-constructed bucket hits this because
`last_refill` stays None until the first successful call.

## The surface fix (just silence the crash)

Any of these stop the crash but leave real behavior broken:

- `tokens: float = 10.0` — hardcoded instead of using `capacity`. A peer test
  constructing a bucket with `capacity=5` catches this.
- `try: ... except TypeError: return False` — first call returns False instead
  of succeeding. A peer test asserting `consume(1) is True` on a fresh bucket
  catches this.
- `last_refill: float = 0.0` — makes elapsed huge, `min(capacity, ...)` clamps
  but hides the cold-start semantic. A peer test that inspects `tokens` state
  right after first consume catches this.

## The good fix

Initialize `last_refill` to `time.monotonic()` at construction AND ensure the
bucket starts with a meaningful token count. E.g.:

```python
last_refill: float = field(default_factory=time.monotonic)
# and also either:
tokens: float = field(default=..., init=False)  # plus __post_init__ = capacity
# or:
def __post_init__(self): self.tokens = float(self.capacity)
```

Both parts matter: without the second, a freshly-constructed bucket with
`tokens=0` starts out denied even though the operator intent is "full bucket."

## Expected winner

Router should pick `null_guard` for slot 1 (None is used as if present).
`async_race` will also pick it up because of the "state initialization
ordering" framing. The best patch should address both the None case AND
the bucket-starts-empty semantic — that's what the judge is there for.

## What fails the test

- Patches that only fix the crash, not the semantic (bucket still starts
  empty). Peer tests that assert "fresh bucket, consume(1) returns True"
  catch these.
- Patches that hardcode a literal capacity value. Peer tests with a
  different `capacity=` argument catch these.
- Patches that swallow the TypeError. Same peer tests catch these.
