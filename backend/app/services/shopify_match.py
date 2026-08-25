"""
Pure matching between live Lightspeed special orders and Shopify `SO`-tagged orders.

A Shopify order carries many line SKUs (bundles); matching the specific LS `system_sku`
pinpoints the right order. The ETA is order-level. On top of the SKU, identity signals are
tried strongest-first:

    email + sku  ->  phone + sku  ->  name + sku  ->  sku alone

The first tier with exactly one candidate wins (`shopify_match_basis` records which). A tier
with several candidates is 'ambiguous' — surfaced with its candidate list, never guessed.
The sku-only fallback is allowed **only when the two sides share no comparable identity
signal at all** (one side is effectively anonymous): if both sides carry an email/phone/name
and none of them agreed, an earlier tier would have caught a real match, so a lone sku-level
candidate is treated as ambiguous rather than risking cross-linking two customers who happen
to have ordered the same SKU.

Manual overrides (the `blocked` pair set persisted via so_match_overrides) are honoured by
excluding those Shopify orders from every tier for that SO; forced links are applied by the
caller before auto-matching.

No I/O here — `ShopifyClient.get_open_special_orders()` supplies the rows. Kept pure so it's
trivially unit-testable.
"""
import re
from typing import Any, Dict, FrozenSet, List, Optional, Set

_NON_DIGITS = re.compile(r"\D+")


def _norm_email(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _norm_sku(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _norm_phone(value: Optional[str]) -> str:
    """Digits only, last 10 — collapses +1/formatting differences between LS and Shopify."""
    digits = _NON_DIGITS.sub("", value or "")
    return digits[-10:] if len(digits) >= 10 else digits


def _norm_name(value: Optional[str]) -> str:
    return " ".join((value or "").strip().lower().split())


def build_shopify_index(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Folds the per-(order x sku) Shopify rows into:
      orders        : { order_id: {order_name, email, phone, name, eta, created_at,
                                   fulfillment_status, financial_status, skus:set} }
      by_email_sku  : { (email, sku): set(order_id) }
      by_phone_sku  : { (phone, sku): set(order_id) }
      by_name_sku   : { (name, sku): set(order_id) }
      by_sku        : { sku: set(order_id) }
    """
    orders: Dict[str, Dict[str, Any]] = {}
    by_email_sku: Dict[tuple, Set[str]] = {}
    by_phone_sku: Dict[tuple, Set[str]] = {}
    by_name_sku: Dict[tuple, Set[str]] = {}
    by_sku: Dict[str, Set[str]] = {}
    for r in rows:
        oid = str(r.get("order_id"))
        o = orders.get(oid)
        if o is None:
            o = {
                "order_id": oid,
                "order_name": r.get("order_name"),
                "email": _norm_email(r.get("email")) or None,
                "phone": _norm_phone(r.get("phone")) or None,
                "name": _norm_name(r.get("customer_name")) or None,
                "eta": r.get("eta"),
                "created_at": r.get("created_at"),
                "fulfillment_status": r.get("fulfillment_status"),
                "financial_status": r.get("financial_status"),
                "skus": set(),
                # sku -> units still owed to the customer on THAT line. Order-level status
                # cannot answer "has the customer got this item?" on a multi-line order.
                "unfulfilled_by_sku": {},
            }
            orders[oid] = o
        sku = _norm_sku(r.get("sku"))
        if not sku:
            continue
        o["skus"].add(sku)
        unfulfilled = r.get("unfulfilled_quantity")
        if unfulfilled is not None:
            # Max, not sum: the same SKU can appear on more than one line of an order, and the
            # question is whether ANY of it is still owed.
            prior = o["unfulfilled_by_sku"].get(sku)
            o["unfulfilled_by_sku"][sku] = max(int(unfulfilled), int(prior or 0))
        by_sku.setdefault(sku, set()).add(oid)
        if o["email"]:
            by_email_sku.setdefault((o["email"], sku), set()).add(oid)
        if o["phone"]:
            by_phone_sku.setdefault((o["phone"], sku), set()).add(oid)
        if o["name"]:
            by_name_sku.setdefault((o["name"], sku), set()).add(oid)
    return {
        "orders": orders,
        "by_email_sku": by_email_sku,
        "by_phone_sku": by_phone_sku,
        "by_name_sku": by_name_sku,
        "by_sku": by_sku,
    }


def candidate_summary(index: Dict[str, Any], order_ids) -> List[Dict[str, Any]]:
    """Compact, JSON-safe candidate descriptions for the UI's ambiguity-resolution picker."""
    out = []
    for oid in sorted(order_ids):
        o = index["orders"].get(oid) or {}
        out.append({
            "order_id": oid,
            "order_name": o.get("order_name"),
            "customer_email": o.get("email"),
            "shopify_expected_date": o.get("eta"),
            "created_at": o.get("created_at"),
        })
    return out


def _none() -> Dict[str, Any]:
    return {
        "shopify_match": "none",
        "shopify_match_basis": None,
        "shopify_order_id": None,
        "shopify_order_name": None,
        "shopify_expected_date": None,
        "shopify_fulfillment_status": None,
        "shopify_financial_status": None,
        "shopify_order_created_at": None,
        "shopify_line_unfulfilled": None,
        "shopify_candidates": [],
        "_candidates": set(),
    }


def _matched(index: Dict[str, Any], oid: str, basis: str,
             sku: Optional[str] = None) -> Dict[str, Any]:
    o = index["orders"][oid]
    return {
        "shopify_match": "matched",
        "shopify_match_basis": basis,
        "shopify_order_id": oid,
        "shopify_order_name": o.get("order_name"),
        "shopify_expected_date": o.get("eta"),
        "shopify_fulfillment_status": o.get("fulfillment_status"),
        "shopify_financial_status": o.get("financial_status"),
        # When the customer actually placed the order. The SO tag (and the Lightspeed special
        # order behind it) is sometimes added days later, so this is the earlier, truer start of
        # the customer's wait -- see so_sla_service.compute_open_clock.
        "shopify_order_created_at": o.get("created_at"),
        # Units of THIS special order's SKU the customer is still owed. None when unknown
        # (a manual link resolved outside the main index, or a pre-upgrade cached payload).
        "shopify_line_unfulfilled": (o.get("unfulfilled_by_sku") or {}).get(sku) if sku else None,
        "shopify_candidates": [],
        "_candidates": {oid},
    }


def _ambiguous(index: Dict[str, Any], candidates: Set[str], basis: str) -> Dict[str, Any]:
    return {
        "shopify_match": "ambiguous",
        "shopify_match_basis": basis,
        "shopify_order_id": None,
        "shopify_order_name": None,
        "shopify_expected_date": None,
        "shopify_fulfillment_status": None,
        "shopify_financial_status": None,
        "shopify_order_created_at": None,
        "shopify_line_unfulfilled": None,
        "shopify_candidates": candidate_summary(index, candidates),
        "_candidates": set(candidates),
    }


def manual_match(index: Dict[str, Any], order_id: str,
                 system_sku: Any = None) -> Dict[str, Any]:
    """A human-forced link (so_match_overrides). The order must exist in the index."""
    return _matched(index, order_id, "manual", _norm_sku(system_sku) or None)


def match_special_order(
    customer_email: Optional[str],
    system_sku: Any,
    index: Dict[str, Any],
    customer_phone: Optional[str] = None,
    customer_name: Optional[str] = None,
    blocked: FrozenSet[str] = frozenset(),
) -> Dict[str, Any]:
    """
    Returns the Shopify enrichment for one LS special order:
      { shopify_match: 'matched'|'ambiguous'|'none', shopify_match_basis,
        shopify_order_id, shopify_order_name, shopify_expected_date,
        shopify_candidates: [...], _candidates: set(order_id) }

    `blocked` is the set of Shopify order ids manually unlinked from this SO — they are
    invisible to every tier. `_candidates` is internal — the caller accumulates the ids of
    *definite* matches to know which Shopify orders were consumed (so they don't also
    surface as "unmatched"); ambiguous candidates are intentionally NOT consumed.
    """
    sku = _norm_sku(system_sku)
    if not sku:
        return _none()

    email = _norm_email(customer_email)
    phone = _norm_phone(customer_phone)
    name = _norm_name(customer_name)

    tiers = [
        ("email_sku", index["by_email_sku"].get((email, sku)) if email else None),
        ("phone_sku", index["by_phone_sku"].get((phone, sku)) if phone else None),
        ("name_sku", index["by_name_sku"].get((name, sku)) if name else None),
    ]
    for basis, cands in tiers:
        cands = (cands or set()) - blocked
        if len(cands) == 1:
            return _matched(index, next(iter(cands)), basis, sku)
        if len(cands) > 1:
            return _ambiguous(index, cands, basis)

    cands = (index["by_sku"].get(sku) or set()) - blocked
    if not cands:
        return _none()
    if len(cands) > 1:
        return _ambiguous(index, cands, "sku_only")

    # Single sku-level candidate. Only auto-accept when the two sides share no comparable
    # identity signal (if they did, it disagreed — the tiers above would have matched it).
    (oid,) = tuple(cands)
    o = index["orders"][oid]
    comparable = (
        (email and o.get("email"))
        or (phone and o.get("phone"))
        or (name and o.get("name"))
    )
    if comparable:
        return _ambiguous(index, cands, "sku_conflict")
    return _matched(index, oid, "sku_only", sku)


def shopify_only_orders(
    index: Dict[str, Any], matched_order_ids: Set[str]
) -> List[Dict[str, Any]]:
    """
    The Shopify `SO`-tagged orders no LS special order definitively claimed — the
    "Unmatched" population (one record per order).
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
