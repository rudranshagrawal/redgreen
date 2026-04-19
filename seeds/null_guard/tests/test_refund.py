"""Happy-path tests that should keep passing after the fix.

Every test uses a non-None refund_amount so the file passes pytest both
before and after the bug is fixed. The None case is tested by the
generated RED test; these exist to make the *regression gate* meaningful
— a patch that fixes the None crash but breaks any of these is a patch
that broke something unrelated to the target bug.
"""

from decimal import Decimal

import pytest

from payments.refund import compute_refund_cents


# ---------- core arithmetic ----------

def test_full_refund():
    assert compute_refund_cents(5000, Decimal("12.50")) == 1250


def test_refund_with_cents():
    # Realistic amount with cents — catches patches that accidentally
    # drop the Decimal wrapper (float * 100 has precision drift).
    assert compute_refund_cents(100_000, Decimal("19.99")) == 1999


def test_zero_refund_returns_zero():
    # Zero refund is legitimate — catches hacks like
    # `refund_amount = refund_amount or Decimal("0")` that special-case
    # falsy values.
    assert compute_refund_cents(5000, Decimal("0")) == 0


# ---------- cap semantics ----------

def test_refund_capped_at_order_total():
    # $50 refund requested but order was only $5 — cap wins.
    assert compute_refund_cents(500, Decimal("50.00")) == 500


def test_exact_order_total_refund():
    # Refund exactly equals order total — should return the total
    # unchanged, not accidentally double-cap.
    assert compute_refund_cents(2500, Decimal("25.00")) == 2500


# ---------- edge semantics ----------

def test_rounding_truncates_fractional_cents():
    # int(Decimal * 100) truncates toward zero, not rounds. Locks in the
    # existing contract so a patch that introduces rounding (Decimal
    # .quantize, round(), etc.) breaks here instead of silently shifting
    # money around.
    assert compute_refund_cents(100_000, Decimal("12.995")) == 1299


def test_negative_refund_raises():
    # Negative refunds are a domain error. Catches patches that widen the
    # contract by silently clamping to zero.
    with pytest.raises(ValueError):
        compute_refund_cents(5000, Decimal("-1.00"))
