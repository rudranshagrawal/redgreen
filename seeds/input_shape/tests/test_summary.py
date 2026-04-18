"""Happy-path tests — must stay green after the fix."""

from billing.summary import LineItem, compute_total


def test_total_of_dataclass_items():
    items = [LineItem(price=1000, quantity=2), LineItem(price=250, quantity=4)]
    assert compute_total(items) == 3000


def test_empty_cart():
    assert compute_total([]) == 0
