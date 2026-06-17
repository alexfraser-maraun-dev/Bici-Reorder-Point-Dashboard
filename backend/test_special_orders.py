"""
Proves the special-order data path against the live Lightspeed account before any UI work:
  SpecialOrder (completed=false) --> OrderLine.orderID --> Order.arrivalDate (expected date)
and the derived overdue/aging fields.

Run:  python test_special_orders.py
"""
import os
from dotenv import load_dotenv
load_dotenv()

from app.services.lightspeed_client import LightspeedClient
from app.services.special_order_service import get_special_order_dashboard


def main():
    print("--- Special Order pipeline smoke test ---")
    client = LightspeedClient()

    # 1. Raw SpecialOrder fetch — also surfaces the OAuth-scope risk (401/403).
    raw = client.get_special_orders()
    print(f"Fetched {len(raw)} open special orders from Lightspeed.")
    if raw:
        sample = raw[0]
        print(f"  sample specialOrderID={sample.get('specialOrderID')} "
              f"status={sample.get('status')} "
              f"orderID={(sample.get('OrderLine') or {}).get('orderID')}")

    # 2. Full normalized dashboard payload (incl. Order.arrivalDate + days_overdue).
    result = get_special_order_dashboard(client)
    summary = result["summary"]
    print("\nSummary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    print("\nTop rows (most overdue first):")
    for o in result["orders"][:10]:
        print(
            f"  SO {o['special_order_id']:>6} | {o['status']:<16} | "
            f"expected={o['expected_date'] or '—':<10} | "
            f"days_overdue={str(o['days_overdue']):>4} | bucket={o['aging_bucket']:<10} | "
            f"overdue={o['is_overdue']!s:<5} | {(o['description'] or '')[:40]}"
        )

    # 3. Sanity assertions on the overdue rule.
    bad = [o for o in result["orders"] if o["is_overdue"] and (o["po_complete"] or o["received_started"])]
    assert not bad, f"{len(bad)} rows flagged overdue despite PO receiving started/complete"
    no_eta = [o for o in result["orders"] if o["no_eta"]]
    print(f"\nOK: {len(no_eta)} no-ETA rows; no overdue row has receiving started/complete.")


if __name__ == "__main__":
    main()
