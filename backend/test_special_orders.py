"""
Proves the special-order data path against the live Lightspeed account:
  SpecialOrder (completed=false) --> OrderLine.orderID --> Order.arrivalDate (expected date)
plus the two-axis triage (procurement_stage + flag), the Shopify match enrichment, the
workorder linkage (SaleLine.saleID -> Workorder), and the manual match-override plumbing.

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

    # 2. Workorder linkage probe: SaleLine.saleID -> Workorder.
    sale_ids = [
        (so.get("SaleLine") or {}).get("saleID")
        for so in raw
        if (so.get("SaleLine") or {}).get("saleID")
    ]
    wo_map = client.get_workorders_by_sale_ids(sale_ids)
    print(f"Workorder probe: {len(set(sale_ids))} sales -> {len(wo_map)} with workorders.")
    for sid, wo in list(wo_map.items())[:3]:
        print(f"  saleID={sid} -> WO #{wo['workorder_id']} status={wo.get('status')}")

    # 3. Full normalized dashboard payload.
    result = get_special_order_dashboard(client)
    summary = result["summary"]
    print("\nSummary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    print("\nTop rows (highest flag severity first):")
    for o in result["orders"][:10]:
        print(
            f"  SO {str(o['special_order_id']):>6} | {o['procurement_stage']:<12} | "
            f"flag={o['flag']:<12} | days_overdue={str(o['days_overdue']):>4} | "
            f"shopify={o['shopify_match']:<9} basis={str(o['shopify_match_basis']):<10} | "
            f"WO={str(o['workorder_id'] or '—'):<6} | {(o['description'] or '')[:36]}"
        )

    orders = result["orders"]
    shopify_only = result["shopify_only"]

    # 4. Sanity assertions on the triage + matching invariants.
    bad = [o for o in orders if o["is_overdue"] != (o["flag"] in ("overdue", "overdue_mid", "critical"))]
    assert not bad, f"{len(bad)} rows where is_overdue disagrees with the flag"

    bad = [o for o in orders if o["shopify_match"] == "matched" and not o["shopify_order_id"]]
    assert not bad, f"{len(bad)} matched rows missing shopify_order_id"

    bad = [o for o in orders if o["shopify_match"] == "ambiguous" and not o["shopify_candidates"]]
    assert not bad, f"{len(bad)} ambiguous rows missing their candidate list"

    # Ambiguous candidates must never be silently hidden: every candidate order id of an
    # ambiguous SO must appear in the shopify_only population (unless a different SO
    # definitively claimed it).
    matched_ids = {o["shopify_order_id"] for o in orders if o["shopify_order_id"]}
    unmatched_ids = {u["order_id"] for u in shopify_only}
    for o in orders:
        if o["shopify_match"] != "ambiguous":
            continue
        for cand in o["shopify_candidates"]:
            oid = cand["order_id"]
            assert oid in matched_ids or oid in unmatched_ids, (
                f"ambiguous candidate {oid} (SO {o['special_order_id']}) vanished from the board"
            )

    ambiguous_flagged = [u for u in shopify_only if u.get("ambiguous_candidate")]
    print(f"\nOK: {len(orders)} rows, {len(shopify_only)} shopify-only "
          f"({len(ambiguous_flagged)} possible-match), invariants hold.")


if __name__ == "__main__":
    main()
