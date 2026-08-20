"""Which purchase order should this special order go on?

Recommend-only by construction. Phase 0 established that Lightspeed cannot perform the
allocation over the API — `SpecialOrder` is read-only on every verb and both surfaces, and
creating the PO line does not make Lightspeed link the special order. So this service ranks the
options and explains itself; the buyer makes the final click in Lightspeed and the next sweep
verifies `orderLineID` actually moved.

Tiers, best first. The ordering follows the real pool rather than intuition: there are ~1,700
already-placed POs with remaining units against only ~64 unsent drafts, so "join something
already in flight" is the workhorse and "add to a draft" is the rarer case.

    in_stock   Already sellable at this store -- do not order at all.
    transfer   Sellable at the sister store (Victoria <-> Langford only).
    inbound_po An ordered, unreceived PO already carries unclaimed units of this exact item.
    draft_po   An unsent draft at this store for a vendor that can supply the brand.
    new_po     Nothing suitable; the buyer needs to raise one.

Tier 0 earns its place empirically: 5 of 37 unallocated special orders were already in stock at
their own store (one with 44 units on hand), plus a Langford->Victoria transfer candidate. Staff
are not immune to skipping the inventory check.
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

# Physical adjacency, not a distance calculation. Victoria (2) and Langford (20) are close
# enough that a transfer beats a purchase order; Adanac (3) is not, and must never suggest or
# receive one.
SISTER_STORE = {"2": "20", "20": "2"}

TIER_ORDER = ["in_stock", "transfer", "inbound_po", "draft_po", "new_po"]


def _parse(value: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _qualifying_vendor_ids(row: Dict[str, Any]) -> List[str]:
    """Vendors that can supply this SKU's brand, fastest first (from `available_vendors`)."""
    return [str(v["vendor_id"]) for v in (row.get("available_vendors") or []) if v.get("vendor_id")]


def build_context(rows: List[Dict[str, Any]], *, stock, unallocated, open_orders, cadence):
    """Bundle the shared lookups once so a batch of rows does not re-query per row."""
    return {"stock": stock, "unallocated": unallocated, "open_orders": open_orders,
            "cadence": cadence}


def _in_stock_candidates(row, ctx, needed):
    item = str(row.get("item_id") or "")
    shop = str(row.get("shop_id") or "")
    out = []
    own = (ctx["stock"] or {}).get((item, shop))
    if own and own.get("sellable", 0) >= needed:
        out.append({
            "tier": "in_stock", "shop_id": shop, "store": row.get("store"),
            "sellable": own["sellable"], "qoh": own.get("qoh"),
            "eta": date.today().isoformat(),
        })
    sister = SISTER_STORE.get(shop)
    if sister:
        far = (ctx["stock"] or {}).get((item, sister))
        if far and far.get("sellable", 0) >= needed:
            out.append({
                "tier": "transfer", "shop_id": sister,
                "sellable": far["sellable"], "qoh": far.get("qoh"),
                # A transfer between the two Island stores is same-week, not same-day.
                "eta": (date.today() + timedelta(days=2)).isoformat(),
            })
    return out


def _inbound_candidates(row, ctx, needed):
    item = str(row.get("item_id") or "")
    shop = str(row.get("shop_id") or "")
    out = []
    for line in (ctx["unallocated"] or {}).get((item, shop), []):
        if line.get("unallocated_units", 0) < needed:
            continue
        eta = line.get("expected_arrival_at")
        out.append({
            "tier": "inbound_po", "order_id": line["order_id"],
            "order_line_id": line.get("order_line_id"),
            "reference_number": line.get("reference_number"),
            "vendor_id": line.get("vendor_id"), "vendor_name": line.get("vendor_name"),
            "unallocated_units": line["unallocated_units"],
            "eta": eta,
            # A PO whose ETA has already passed is late, not fast. Surfaced so the buyer does
            # not treat a stale date as a delivery promise.
            "eta_overdue": bool(eta and _parse(eta) and _parse(eta) < date.today()),
        })
    return out


def _draft_candidates(row, ctx):
    """Unsent drafts at this store for a vendor that can supply the brand.

    Restricted to `unsent` deliberately: adding a line to an already-placed PO would change an
    order the vendor has seen, which `po_service._appendable_orders` has always forbidden.
    """
    shop = str(row.get("shop_id") or "")
    qualifying = _qualifying_vendor_ids(row)
    vendor_rank = {v: i for i, v in enumerate(qualifying)}
    out = []
    for order in ctx["open_orders"] or []:
        if str(order.get("shopID")) != shop or order.get("po_state") != "unsent":
            continue
        vid = str(order.get("vendorID") or "")
        if qualifying and vid not in vendor_rank:
            continue
        cad = (ctx["cadence"] or {}).get((shop, vid)) or {}
        out.append({
            "tier": "draft_po", "order_id": str(order.get("orderID")),
            "reference_number": order.get("refNum"),
            "vendor_id": vid, "vendor_name": (order.get("Vendor") or {}).get("name"),
            "created_at": order.get("createTime"),
            "vendor_rank": vendor_rank.get(vid, 99),
            "cadence_days": cad.get("cadence_days"),
            "next_order_date": cad.get("next_expected_order_date"),
            "eta": None,
        })
    out.sort(key=lambda c: c["vendor_rank"])
    return out


def recommend(row: Dict[str, Any], ctx: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
    """The best option for one special order, plus ranked alternatives and a reason."""
    today = today or date.today()
    needed = int(row.get("unit_quantity") or 1)
    promise = _parse(row.get("promise_date"))

    candidates = (_in_stock_candidates(row, ctx, needed)
                  + _inbound_candidates(row, ctx, needed)
                  + _draft_candidates(row, ctx))

    for c in candidates:
        eta = _parse(c.get("eta"))
        c["meets_promise"] = None if not (promise and eta) else eta <= promise

    # Tier order first -- a tier is a statement about *kind* of option, and a cheaper kind wins
    # even when a later one happens to have a nearer date. Within a tier, soonest arrival.
    candidates.sort(key=lambda c: (
        TIER_ORDER.index(c["tier"]),
        c.get("eta") or "9999-12-31",
        c.get("vendor_rank", 0),
    ))

    if not candidates:
        fastest = (row.get("available_vendors") or [None])[0]
        shop = str(row.get("shop_id") or "")
        vid = str((fastest or {}).get("vendor_id") or "")
        cad = (ctx["cadence"] or {}).get((shop, vid)) or {}
        return {
            "tier": "new_po",
            "recommendation": {
                "tier": "new_po",
                "vendor_id": vid or None,
                "vendor_name": (fastest or {}).get("vendor_name"),
                "lead_time_days": (fastest or {}).get("lead_time_days"),
                "cadence_days": cad.get("cadence_days"),
                "next_order_date": cad.get("next_expected_order_date"),
            },
            "alternatives": [],
            "reason": _new_po_reason(fastest, cad, promise, today),
        }

    best = candidates[0]
    return {
        "tier": best["tier"],
        "recommendation": best,
        "alternatives": candidates[1:6],
        "reason": _reason(best, row, needed, promise),
    }


def _new_po_reason(fastest, cad, promise, today) -> str:
    if not fastest:
        return ("No qualifying vendor found for this brand and nothing in stock or inbound — "
                "needs a buyer to choose a source.")
    bits = [f"No suitable PO — raise one with {fastest.get('vendor_name')}"]
    if fastest.get("lead_time_days") is not None:
        bits.append(f"lead time {fastest['lead_time_days']}d")
    if cad.get("next_expected_order_date"):
        bits.append(f"next {fastest.get('vendor_name')} order due {cad['next_expected_order_date']}"
                    + (f" (~every {cad['cadence_days']}d)" if cad.get("cadence_days") else ""))
    return " · ".join(bits)


def _reason(best, row, needed, promise) -> str:
    if best["tier"] == "in_stock":
        return (f"Already in stock at {row.get('store') or 'this store'} — {best['sellable']} "
                f"sellable, {needed} needed. Confirm on the shelf before ordering.")
    if best["tier"] == "transfer":
        return (f"{best['sellable']} sellable at the sister store — transfer rather than order "
                f"(Victoria and Langford are close enough for this to beat a PO).")
    if best["tier"] == "inbound_po":
        late = " — but that date has passed, so treat it as late" if best.get("eta_overdue") else ""
        meets = ""
        if best.get("meets_promise") is False:
            meets = " — arrives after the customer promise"
        return (f"PO {best.get('reference_number') or best['order_id']} "
                f"({best.get('vendor_name')}) already has {best['unallocated_units']} unclaimed "
                f"unit(s) inbound, due {best.get('eta') or 'no ETA'}{late}{meets}.")
    if best["tier"] == "draft_po":
        cad = f" · next order due {best['next_order_date']}" if best.get("next_order_date") else ""
        return (f"Add to unsent draft PO {best.get('reference_number') or best['order_id']} "
                f"({best.get('vendor_name')}){cad}.")
    return "No recommendation."


def list_candidate_pos(open_orders: List[Dict[str, Any]], shop_id: str) -> List[Dict[str, Any]]:
    """POs a buyer may pick when overriding the recommendation, for one store.

    Unsent drafts are appendable. Ordered-but-incomplete POs are offered too, because a special
    order can legitimately be satisfied by units already on one — but they are labelled, so
    nobody adds a line to an order the vendor has already received.
    """
    out = []
    for order in open_orders or []:
        if str(order.get("shopID")) != str(shop_id):
            continue
        state = order.get("po_state")
        if state not in ("unsent", "ordered", "partially_received"):
            continue
        out.append({
            "order_id": str(order.get("orderID")),
            "reference_number": order.get("refNum"),
            "vendor_id": str(order.get("vendorID") or ""),
            "vendor_name": (order.get("Vendor") or {}).get("name"),
            "po_state": state,
            "appendable": state == "unsent",
            "ordered_date": order.get("orderedDate"),
            "expected_date": order.get("arrivalDate"),
        })
    out.sort(key=lambda o: (not o["appendable"], o.get("vendor_name") or ""))
    return out
