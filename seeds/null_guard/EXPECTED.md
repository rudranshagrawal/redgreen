# null_guard seed

A payments service that crashes on legitimate `None` input.

## How to reproduce

```
cd seeds/null_guard
python -m pytest tests/          # existing tests pass
python crash.py                  # raises TypeError
```

## Expected exception

```
TypeError: unsupported operand type(s) for *: 'NoneType' and 'decimal.Decimal'
```

at `src/payments/refund.py:21` inside `compute_refund_cents`.

## Expected winner

`null_guard` hypothesis — the fix is a guard clause that returns `0`
(or raises a domain `RefundError`) when `refund_amount is None`.

## Expected patch shape

A one-line `if refund_amount is None:` guard at the top of
`compute_refund_cents`. +3 / -1 lines. No change to tests/ — the
generated test is *new*, reproducing the crash.

## Why this seed

The whole RedGreen thesis collapses if the fix-first model is wrong.
`null_guard` is the canonical "language wart" bug: super common, super
boring, and exactly the kind of thing where a strong LLM should
outperform a weaker one on its first try.
