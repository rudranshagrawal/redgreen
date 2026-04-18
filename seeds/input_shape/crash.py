"""Reproduce the input-shape bug.

Legacy webhook handler hands us dicts instead of LineItem objects —
compute_total should either coerce them or fail with a clear domain
error, but today it explodes with AttributeError.
"""

from src.billing.summary import compute_total


def main() -> None:
    legacy_cart = [
        {"price": 1000, "quantity": 2},
        {"price": 250, "quantity": 4},
    ]
    print("total:", compute_total(legacy_cart))


if __name__ == "__main__":
    main()
