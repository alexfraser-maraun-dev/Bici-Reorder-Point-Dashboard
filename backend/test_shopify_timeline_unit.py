"""Offline unit tests for the Shopify order-timeline reader.

The network call is stubbed; what is under test is the parsing, the HTML stripping and the
curation rule, all of which are pure and all of which have bitten before.
"""

import sys

sys.path.insert(0, ".")
from app.services.shopify_client import ShopifyClient, _strip_html  # noqa: E402


def _client_returning(nodes, has_next=False):
    client = ShopifyClient.__new__(ShopifyClient)   # no __init__: no credentials needed
    client._graphql = lambda query, variables=None: {
        "node": {"id": "gid://shopify/Order/1", "name": "#245382",
                 "events": {"pageInfo": {"hasNextPage": has_next}, "nodes": nodes}}
    }
    return client


def _event(action, message, typename="BasicEvent", critical=False, app=None, eid="9"):
    return {"__typename": typename, "id": f"gid://shopify/Event/{eid}", "action": action,
            "createdAt": "2026-08-01T20:43:00Z", "message": message,
            "criticalAlert": critical, "appTitle": app}


def test_markup_is_stripped_not_rendered():
    # Shopify embeds anchors in payout and confirmation lines. Rendering the raw string would
    # mean dangerouslySetInnerHTML on text that originates outside this app.
    raw = '$340.45 CAD was added to your <a href="https://x.myshopify.com/admin/payments">payout</a>.'
    assert _strip_html(raw) == "$340.45 CAD was added to your payout."
    assert _strip_html("Ada &amp; Co. &lt;tag&gt;") == "Ada & Co. <tag>"
    assert _strip_html(None) == ""
    assert _strip_html("") == ""
    print("test_markup_is_stripped_not_rendered OK")


def test_curation_allows_unknown_actions_through():
    nodes = [
        _event("comment", "Tires were marked as fulfilled but we could not find on shelf.",
               typename="CommentEvent", app="Shopify Web", eid="1"),
        _event("fulfillment_cancelled", "Ryan canceled fulfillment via Manual for 2 items.", eid="2"),
        _event("payments_charge", "$340.45 CAD was added to your payout.", eid="3"),
        _event("authorization_success", "$348.24 CAD was authorized.", eid="4"),
        # An action this code has never seen. It must SURFACE, not hide: on a triage panel an
        # unknown event is far more likely to be signal than payment plumbing.
        _event("some_action_shopify_added_last_week", "Something new happened.", eid="5"),
    ]
    out = _client_returning(nodes).get_order_timeline("1")

    assert out["order_name"] == "#245382" and out["truncated"] is False
    by_id = {e["id"]: e for e in out["events"]}
    assert by_id["1"]["is_comment"] is True
    assert by_id["2"]["is_comment"] is False
    assert [e["id"] for e in out["events"] if e["noise"]] == ["3", "4"]
    assert by_id["5"]["noise"] is False, "an unknown action must not be silently hidden"
    assert by_id["3"]["message"] == "$340.45 CAD was added to your payout."
    print("test_curation_allows_unknown_actions_through OK")


def test_a_critical_alert_is_never_noise():
    # Whatever its action says, a critical alert is the one thing that must not be filtered out.
    nodes = [_event("payments_charge", "Chargeback opened.", critical=True, eid="7")]
    out = _client_returning(nodes).get_order_timeline("1")
    assert out["events"][0]["critical"] is True
    assert out["events"][0]["noise"] is False
    print("test_a_critical_alert_is_never_noise OK")


def test_truncation_is_reported():
    out = _client_returning([_event("comment", "hi", typename="CommentEvent")], has_next=True)
    assert out.get_order_timeline("1")["truncated"] is True
    print("test_truncation_is_reported OK")


def test_missing_order_yields_an_empty_timeline_not_a_crash():
    client = ShopifyClient.__new__(ShopifyClient)
    client._graphql = lambda query, variables=None: {"node": None}
    out = client.get_order_timeline("1")
    assert out["events"] == [] and out["order_name"] is None
    print("test_missing_order_yields_an_empty_timeline_not_a_crash OK")


if __name__ == "__main__":
    test_markup_is_stripped_not_rendered()
    test_curation_allows_unknown_actions_through()
    test_a_critical_alert_is_never_noise()
    test_truncation_is_reported()
    test_missing_order_yields_an_empty_timeline_not_a_crash()
    print("\nAll Shopify timeline unit tests passed.")
