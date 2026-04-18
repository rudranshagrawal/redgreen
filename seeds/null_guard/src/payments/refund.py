"""Refund math for the payments service.

Bug: `compute_refund_cents` crashes when `refund_amount` is None — which
happens legitimately when a merchant records a "refund pending review".
A correct implementation treats None as zero-refund (or raises a domain
error), never a TypeError from arithmetic on None.
"""

from __future__ import annotations

from decimal import Decimal


def compute_refund_cents(order_total_cents: int, refund_amount: Decimal | None) -> int:
    """Convert a decimal refund amount (dollars) to cents, capped at the order total.

    Returns 0 when no refund is requested.
    """
    # BUG: when refund_amount is None this raises TypeError because
    # Decimal(None) blows up and `None * 100` is unsupported.
    cents = int(refund_amount * Decimal(100))
    if cents < 0:
        raise ValueError("refund cannot be negative")
    return min(cents, order_total_cents)
