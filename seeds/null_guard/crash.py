"""Reproducible entry point for the demo: hit Debug and watch it crash.

Usage:
    python crash.py

Expected: TypeError at payments/refund.py inside compute_refund_cents,
triggered by the merchant's "pending review" record having refund_amount=None.
"""

from decimal import Decimal

from src.payments.refund import compute_refund_cents


def main() -> None:
    pending_review_order = {"order_total_cents": 5000, "refund_amount": None}
    approved_order = {"order_total_cents": 5000, "refund_amount": Decimal("12.50")}

    for order in (approved_order, pending_review_order):
        cents = compute_refund_cents(order["order_total_cents"], order["refund_amount"])
        print(f"refund={cents} cents for {order}")


if __name__ == "__main__":
    main()
