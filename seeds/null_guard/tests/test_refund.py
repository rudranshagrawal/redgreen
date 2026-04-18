"""Happy-path tests that should keep passing after the fix."""

from decimal import Decimal

from payments.refund import compute_refund_cents


def test_full_refund():
    assert compute_refund_cents(5000, Decimal("12.50")) == 1250


def test_refund_capped_at_order_total():
    assert compute_refund_cents(500, Decimal("50.00")) == 500
