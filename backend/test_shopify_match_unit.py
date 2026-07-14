"""
Offline unit tests for the tiered LS<->Shopify special-order matcher and the enrichment
merge (ambiguity surfacing + manual overrides). Pure logic — no Lightspeed/Shopify/BigQuery.

Run:  python test_shopify_match_unit.py
"""
from datetime import date

from app.services import shopify_match
from app.services.special_order_service import _enrich_with_shopify


def _row(order_id, sku, email=None, phone=None, name=None, eta=None):
    return {
        "order_id": order_id,
        "order_name": f"#{order_id}",
        "email": email,
        "phone": phone,
        "customer_name": name,
        "eta": eta,
        "created_at": "2026-07-01T00:00:00Z",
        "fulfillment_status": "UNFULFILLED",
        "financial_status": "PAID",
        "sku": sku,
    }


def _ls_order(so_id, sku, email=None, phone=None, name=None):
    """A minimal normalized LS SO row (the fields enrichment reads/writes)."""
    return {
        "special_order_id": so_id,
        "system_sku": sku,
        "customer_email": email,
        "customer_phone": phone,
        "customer_name": name,
        "procurement_stage": "ordered",
        "days_since_creation": 3,
        "contacted": False,
        "expected_date": None,
        "flag": "none",
        "days_overdue": None,
        "is_overdue": False,
        "shopify_match": "none",
        "shopify_match_basis": None,
        "shopify_order_id": None,
        "shopify_order_name": None,
        "shopify_order_url": None,
        "shopify_expected_date": None,
        "shopify_candidates": [],
    }


def test_tiers():
    index = shopify_match.build_shopify_index([
        _row("100", "SKU1", email="a@x.com", phone="+1 (250) 555-0100", name="Alice Ant"),
        _row("200", "SKU1", email="b@x.com", phone="250-555-0200", name="Bob Bee"),
        _row("300", "SKU2", email=None, phone=None, name=None),
        _row("400", "SKU3", email="c@x.com", name="Cara Cat"),
    ])

    # Tier 1: email + sku.
    m = shopify_match.match_special_order("A@X.com ", "SKU1", index)
    assert (m["shopify_match"], m["shopify_match_basis"], m["shopify_order_id"]) == ("matched", "email_sku", "100"), m

    # Tier 2: phone + sku (different email, formatting noise on the phone).
    m = shopify_match.match_special_order("other@y.com", "SKU1", index, customer_phone="12505550200")
    assert (m["shopify_match"], m["shopify_match_basis"], m["shopify_order_id"]) == ("matched", "phone_sku", "200"), m

    # Tier 3: name + sku.
    m = shopify_match.match_special_order(None, "SKU1", index, customer_name="  bob   BEE ")
    assert (m["shopify_match"], m["shopify_match_basis"], m["shopify_order_id"]) == ("matched", "name_sku", "200"), m

    # Sku-only allowed: the Shopify order is anonymous, so nothing comparable conflicts.
    m = shopify_match.match_special_order("who@ever.com", "SKU2", index, customer_name="Zed Zebra")
    assert (m["shopify_match"], m["shopify_match_basis"], m["shopify_order_id"]) == ("matched", "sku_only", "300"), m

    # Sku-only DEMOTED: both sides have identity signals and none agree -> ambiguous,
    # never a silent cross-customer link.
    m = shopify_match.match_special_order("zed@z.com", "SKU3", index, customer_name="Zed Zebra")
    assert (m["shopify_match"], m["shopify_match_basis"]) == ("ambiguous", "sku_conflict"), m
    assert [c["order_id"] for c in m["shopify_candidates"]] == ["400"]

    # Anonymous LS side (no email/phone/name) can still take the unique sku hit.
    m = shopify_match.match_special_order(None, "SKU3", index)
    assert (m["shopify_match"], m["shopify_match_basis"], m["shopify_order_id"]) == ("matched", "sku_only", "400"), m

    # Two candidates sharing email+sku -> ambiguous at tier 1, with candidates.
    index2 = shopify_match.build_shopify_index([
        _row("500", "SKU9", email="dup@x.com"),
        _row("600", "SKU9", email="dup@x.com"),
    ])
    m = shopify_match.match_special_order("dup@x.com", "SKU9", index2)
    assert m["shopify_match"] == "ambiguous" and m["shopify_match_basis"] == "email_sku", m
    assert {c["order_id"] for c in m["shopify_candidates"]} == {"500", "600"}

    # Blocking one of them resolves the ambiguity.
    m = shopify_match.match_special_order("dup@x.com", "SKU9", index2, blocked=frozenset({"500"}))
    assert (m["shopify_match"], m["shopify_order_id"]) == ("matched", "600"), m

    print("test_tiers OK")


def test_enrich_ambiguity_surfacing_and_overrides():
    today = date(2026, 7, 13)
    rows = [
        _row("100", "SKU1", email="dup@x.com", eta="2026-07-20"),
        _row("200", "SKU1", email="dup@x.com"),
        _row("300", "SKU2", email="c@x.com", eta="2026-07-01"),
    ]

    # One LS SO ambiguous over orders 100/200; nothing claims 300.
    index = shopify_match.build_shopify_index(rows)
    orders = [_ls_order("9001", "SKU1", email="dup@x.com")]
    unmatched = _enrich_with_shopify(index, orders, [], today)
    assert orders[0]["shopify_match"] == "ambiguous"
    # THE bug-3 fix: both ambiguous candidates still surface (flagged), plus the orphan.
    by_id = {u["order_id"]: u for u in unmatched}
    assert set(by_id) == {"100", "200", "300"}, sorted(by_id)
    assert by_id["100"]["ambiguous_candidate"] and by_id["200"]["ambiguous_candidate"]
    assert not by_id["300"]["ambiguous_candidate"]

    # Manual link resolves the ambiguity: SO 9001 -> order 200 (basis 'manual'),
    # 200 is consumed, 100 stays visible as a plain unmatched order.
    index = shopify_match.build_shopify_index(rows)
    orders = [_ls_order("9001", "SKU1", email="dup@x.com")]
    overrides = {"links": {"9001": "200"}, "blocked": set()}
    unmatched = _enrich_with_shopify(index, orders, [], today, overrides)
    assert (orders[0]["shopify_match"], orders[0]["shopify_match_basis"], orders[0]["shopify_order_id"]) == (
        "matched", "manual", "200"), orders[0]
    assert {u["order_id"] for u in unmatched} == {"100", "300"}

    # Manual unlink: SO 9002 auto-matches order 300 by email+sku; blocking the pair
    # forces it back to none and 300 surfaces as unmatched.
    index = shopify_match.build_shopify_index(rows)
    orders = [_ls_order("9002", "SKU2", email="c@x.com")]
    overrides = {"links": {}, "blocked": {("9002", "300")}}
    unmatched = _enrich_with_shopify(index, orders, [], today, overrides)
    assert orders[0]["shopify_match"] == "none", orders[0]["shopify_match"]
    assert "300" in {u["order_id"] for u in unmatched}

    # A matched SO in the 'ordered' stage inherits the Shopify ETA as its classification
    # date: 2026-07-01 promise vs 2026-07-13 today -> 12 days late -> critical.
    index = shopify_match.build_shopify_index(rows)
    orders = [_ls_order("9002", "SKU2", email="c@x.com")]
    _enrich_with_shopify(index, orders, [], today)
    assert orders[0]["shopify_match"] == "matched"
    assert orders[0]["flag"] == "critical" and orders[0]["days_overdue"] == 12, orders[0]

    # A stale manual link (order id not in the open population) lapses to auto-matching.
    index = shopify_match.build_shopify_index(rows)
    orders = [_ls_order("9002", "SKU2", email="c@x.com")]
    overrides = {"links": {"9002": "999999"}, "blocked": set()}
    _enrich_with_shopify(index, orders, [], today, overrides)
    assert (orders[0]["shopify_match"], orders[0]["shopify_match_basis"]) == ("matched", "email_sku")

    print("test_enrich_ambiguity_surfacing_and_overrides OK")


def test_completed_adoption_requires_definite_match():
    today = date(2026, 7, 13)
    rows = [
        _row("100", "SKU1", email="dup@x.com"),
        _row("200", "SKU1", email="dup@x.com"),
        _row("300", "SKU2", email="c@x.com"),
    ]
    index = shopify_match.build_shopify_index(rows)
    orders = []
    completed = [
        _ls_order("8001", "SKU1", email="dup@x.com"),  # ambiguous -> must NOT adopt
        _ls_order("8002", "SKU2", email="c@x.com"),    # definite -> adopts 300
    ]
    unmatched = _enrich_with_shopify(index, orders, completed, today)
    assert [o["special_order_id"] for o in orders] == ["8002"]
    ids = {u["order_id"] for u in unmatched}
    assert ids == {"100", "200"}, ids

    print("test_completed_adoption_requires_definite_match OK")


def test_override_fold():
    """The append-only override log folds to latest-wins state (mirrors
    bigquery_sync.fetch_so_match_overrides without BigQuery)."""
    rows = [
        {"special_order_id": "1", "shopify_order_id": "A", "action": "link"},
        {"special_order_id": "1", "shopify_order_id": "B", "action": "link"},    # supersedes A
        {"special_order_id": "1", "shopify_order_id": "B", "action": "unlink"},  # kills the link, blocks pair
        {"special_order_id": "2", "shopify_order_id": "C", "action": "unlink"},
        {"special_order_id": "2", "shopify_order_id": "C", "action": "link"},    # re-link unblocks
    ]
    links, blocked = {}, set()
    for r in rows:
        so, oid, action = r["special_order_id"], r["shopify_order_id"], r["action"]
        if action == "link":
            links[so] = oid
            blocked.discard((so, oid))
        else:
            blocked.add((so, oid))
            if links.get(so) == oid:
                del links[so]
    assert links == {"2": "C"}, links
    assert blocked == {("1", "B")}, blocked
    print("test_override_fold OK")


if __name__ == "__main__":
    test_tiers()
    test_enrich_ambiguity_surfacing_and_overrides()
    test_completed_adoption_requires_definite_match()
    test_override_fold()
    print("\nAll shopify-match unit tests passed.")
