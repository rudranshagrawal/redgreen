"""Billing summary.

Bug: `compute_total` expects each item to expose `.price` and `.quantity`
attributes (it was designed around the `LineItem` dataclass), but callers
from the legacy webhook handler pass raw dicts. AttributeError blows up
the whole invoice render.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LineItem:
    price: int   # cents
    quantity: int


def compute_total(items) -> int:
    """Sum of price * quantity for every line item, in cents."""
    total = 0
    for item in items:
        # BUG: dict inputs crash here because dicts have no `.price`.
        total += item.price * item.quantity
    return total
