"""
Pure matching between live Lightspeed special orders and Shopify `SO`-tagged orders.

The join mirrors the BigQuery `ls_special_orders_joined` view: customer **email + SKU**, where
the Shopify SKU on a line equals the Lightspeed `system_sku`. A Shopify order carries many line
SKUs (bundles); matching the specific LS `system_sku` pinpoints the right order. The ETA is
order-level.

No I/O here — `get_shopify_special_orders()` (bigquery_sync) supplies the rows. Kept pure so it's
trivially unit-testable.
"""
from typing import Any, Dict, List, Optional, Set


def _norm_email(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _norm_sku(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def build_shopify_index(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Folds the per-(order x sku) Shopify rows into:
      orders        : { order_id: {order_name, email, eta, created_at, fulfillment_status,
                                   financial_status, skus:set} }
      by_email_sku  : { (email, sku): set(order_id) }
      by_sku        : { sku: set(order_id) }
    """
    orders: Dict[str, Dict[str, Any]] = {}
    by_email_sku: Dict[tuple, Set[str]] = {}
    by_sku: Dict[str, Set[str]] = {}
    for r in rows:
        oid = str(r.get("order_id"))
        o = orders.get(oid)
        if o is None:
            o = {
                "order_id": oid,
                "order_name": r.get("order_name"),
                "email": _norm_email(r.get("email")) or None,
                "eta": r.get("eta"),
                "created_at": r.get("created_at"),
                "fulfillment_status": r.get("fulfillment_status"),
                "financial_status": r.get("financial_status"),
                "skus": set(),
            }
            orders[oid] = o
        sku = _norm_sku(r.get("sku"))
        if not sku:
            continue
        o["skus"].add(sku)
        by_sku.setdefault(sku, set()).add(oid)
        if o["email"]:
            by_email_sku.setdefault((o["email"], sku), set()).add(oid)
    return {"orders": orders, "by_email_sku": by_email_sku, "by_sku": by_sku}


def match_special_order(
    customer_email: Optional[str], system_sku: Any, index: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Returns the Shopify enrichment for one LS special order:
      { shopify_match: 'matched'|'ambiguous'|'none', shopify_order_id, shopify_order_name,
        shopify_expected_date, _candidates: set(order_id) }
    `_candidates` is internal — the caller accumulates it to know which Shopify orders were
    consumed by *some* LS SO (so they don't also surface as "unmatched").

    email+sku is preferred; falls back to sku-only when email is missing on either side. More
    than one distinct candidate order => 'ambiguous' (surfaced, never guessed).
    """
    none = {
        "shopify_match": "none",
        "shopify_order_id": None,
        "shopify_order_name": None,
        "shopify_expected_date": None,
        "_candidates": set(),
    }
    sku = _norm_sku(system_sku)
    if not sku:
        return none
    email = _norm_email(customer_email)

    candidates: Optional[Set[str]] = None
    if email:
        candidates = index["by_email_sku"].get((email, sku))
    if not candidates:  # no email, or no email+sku hit -> fall back to sku alone
        candidates = index["by_sku"].get(sku)
    if not candidates:
        return none

    if len(candidates) > 1:
        return {
            "shopify_match": "ambiguous",
            "shopify_order_id": None,
            "shopify_order_name": None,
            "shopify_expected_date": None,
            "_candidates": set(candidates),
        }

    (oid,) = tuple(candidates)
    o = index["orders"][oid]
    return {
        "shopify_match": "matched",
        "shopify_order_id": oid,
        "shopify_order_name": o.get("order_name"),
        "shopify_expected_date": o.get("eta"),
        "_candidates": {oid},
    }


def shopify_only_orders(
    index: Dict[str, Any], matched_order_ids: Set[str]
) -> List[Dict[str, Any]]:
    """
    The Shopify `SO`-tagged orders that no LS special order referenced — the "Unmatched"
    population (one record per order).
    """
    out: List[Dict[str, Any]] = []
    for oid, o in index["orders"].items():
        if oid in matched_order_ids:
            continue
        out.append({
            "order_id": oid,
            "order_name": o.get("order_name"),
            "customer_email": o.get("email"),
            "shopify_expected_date": o.get("eta"),
            "created_at": o.get("created_at"),
            "fulfillment_status": o.get("fulfillment_status"),
            "financial_status": o.get("financial_status"),
            "skus": sorted(o.get("skus", [])),
        })
    # Stable, useful order: soonest promised date first, then order name.
    out.sort(key=lambda r: (r["shopify_expected_date"] is None, r["shopify_expected_date"] or "", r["order_name"] or ""))
    return out
