"""Per-competitor notification mutes + the reference-source ordering of the
per-item competitor breakdown.

The mute is a display rule enforced at read time, so the things worth pinning
down are: only a literal `true` mutes, a mute never leaks past its own event
family (a store muted for stock noise must still raise MAP violations), the
predicate reaches BOTH read paths that feed the UI badge and Slack, and nothing
is emitted at all when no store is muted (the common case must stay parameter-free).

Live BigQuery isn't exercised (same posture as test_price_intelligence_matrix):
_rows / get_competitors are mocked and the emitted SQL + params are asserted.
"""
import json
import unittest
from unittest.mock import MagicMock, patch

from app.services.price_intelligence import digest, repository


def _competitor(cid, settings=None):
    return {
        "competitor_id": cid,
        "name": f"Store {cid}",
        "settings_json": json.dumps(settings) if settings is not None else None,
    }


class MutedCompetitorTests(unittest.TestCase):
    def test_only_literal_true_mutes(self):
        competitors = [
            _competitor("a", {"mute_price_alerts": True}),
            _competitor("b", {"mute_price_alerts": False}),
            # A truthy string is NOT a mute — the router rejects non-booleans, and
            # a legacy/hand-edited row must fail open (keep alerting) rather than
            # silently swallow a store's price moves.
            _competitor("c", {"mute_price_alerts": "true"}),
            _competitor("d", {"mute_stock_alerts": True}),
            _competitor("e"),
            _competitor("f", {"url_allow_pattern": "/product/"}),
        ]
        with patch.object(repository, "get_competitors", return_value=competitors):
            muted = repository.muted_event_competitors()
        self.assertEqual(["a"], muted["mute_price_alerts"])
        self.assertEqual(["d"], muted["mute_stock_alerts"])

    def test_unparseable_settings_are_ignored(self):
        competitors = [{"competitor_id": "a", "settings_json": "{not json"},
                       {"competitor_id": "b", "settings_json": "[1, 2]"}]
        with patch.object(repository, "get_competitors", return_value=competitors):
            muted = repository.muted_event_competitors()
        self.assertEqual({"mute_price_alerts": [], "mute_stock_alerts": []}, muted)

    def test_no_mutes_emits_no_sql_and_no_params(self):
        with patch.object(repository, "get_competitors",
                          return_value=[_competitor("a")]):
            sql, params = repository.sql_event_mute_filter()
        self.assertEqual("", sql)
        self.assertEqual([], params)

    def test_filter_is_scoped_to_the_muted_family(self):
        with patch.object(repository, "get_competitors", return_value=[
            _competitor("a", {"mute_price_alerts": True}),
            _competitor("b", {"mute_stock_alerts": True}),
        ]):
            sql, params = repository.sql_event_mute_filter()
        # Each clause pairs its own competitors with its own event types, so a
        # price-muted store keeps its stock events (and vice versa), and neither
        # mute touches map_violation / undercut.
        self.assertIn("'price_drop', 'price_increase'", sql)
        self.assertIn("'out_of_stock', 'back_in_stock'", sql)
        self.assertNotIn("map_violation", sql)
        self.assertNotIn("undercut", sql)
        by_name = {p.name: list(p.values) for p in params}
        self.assertEqual([["a"], ["b"]], [by_name["muted_0"], by_name["muted_1"]])

    def test_groups_argument_limits_the_predicate(self):
        with patch.object(repository, "get_competitors", return_value=[
            _competitor("a", {"mute_price_alerts": True}),
            _competitor("b", {"mute_stock_alerts": True}),
        ]):
            sql, params = repository.sql_event_mute_filter(
                groups=("mute_price_alerts",))
        self.assertIn("price_drop", sql)
        self.assertNotIn("out_of_stock", sql)
        self.assertEqual(1, len(params))

    def test_alias_prefixes_the_columns(self):
        with patch.object(repository, "get_competitors",
                          return_value=[_competitor("a", {"mute_price_alerts": True})]):
            sql, _ = repository.sql_event_mute_filter(alias="e")
        self.assertIn("e.competitor_id IN UNNEST(@muted_0)", sql)
        self.assertIn("e.event_type IN", sql)


class MuteReachesEveryReadPathTests(unittest.TestCase):
    """The feed, the unread badge and the Slack digest all have to honour the
    mute — a store hidden from the feed but still driving the badge (or still
    narrated by the digest LLM) would read as a bug, not a mute."""

    def setUp(self):
        self.competitors = [_competitor("a", {"mute_price_alerts": True})]

    def test_change_feed_filters_in_sql_not_after_the_limit(self):
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "get_competitors", return_value=self.competitors), \
             patch.object(repository, "_rows", return_value=[]) as rows:
            repository.get_change_events(days=14, limit=200)
        sql = rows.call_args.args[0]
        self.assertIn("NOT (competitor_id IN UNNEST(@muted_0)", sql)
        # Muted rows must be gone before the LIMIT, or a noisy store eats the page.
        self.assertLess(sql.index("@muted_0"), sql.index("LIMIT"))
        names = [p.name for p in rows.call_args.kwargs["params"]]
        self.assertIn("muted_0", names)

    def test_unacknowledged_count_filters_too(self):
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_cache_get", return_value=None), \
             patch.object(repository, "_cache_set"), \
             patch.object(repository, "get_competitors", return_value=self.competitors), \
             patch.object(repository, "_rows", return_value=[{"n": 0}]) as rows:
            repository.count_unacknowledged_events()
        self.assertIn("@muted_0", rows.call_args.args[0])
        self.assertEqual(["muted_0"],
                         [p.name for p in rows.call_args.kwargs["params"]])

    def test_digest_excludes_price_muted_stores_only(self):
        """The LLM writes the Slack narrative from `notable_changes`, so a muted
        store has to be filtered out of that query too — otherwise the digest
        keeps talking about the moves the user silenced."""
        captured = []

        class _Client:
            def query(self, sql, job_config=None):
                captured.append((sql, job_config))
                return MagicMock(**{"result.return_value": []})

        with patch.object(digest, "get_bq_client", return_value=_Client()), \
             patch.object(repository, "get_tracked_products", return_value=[]), \
             patch.object(repository, "get_competitors", return_value=self.competitors):
            digest.build_digest_stats("run-1")

        changes = [(sql, cfg) for sql, cfg in captured if "pct_change" in sql
                   and "event_type IN" in sql]
        self.assertEqual(1, len(changes))
        sql, cfg = changes[0]
        self.assertIn("NOT (competitor_id IN UNNEST(@muted_0)", sql)
        self.assertIn("'price_drop', 'price_increase'", sql)
        # Only the price family: a price mute must never suppress MAP violations
        # or undercuts, which the same query carries.
        self.assertNotIn("out_of_stock", sql)
        names = [p.name for p in cfg.query_parameters]
        # run_id still rides along — the mute params are additive, not a swap.
        self.assertEqual(["run_id", "muted_0"], names)


class BreakdownOrderingTests(unittest.TestCase):
    """Google Merchant rows are reference statistics, not shelf prices — they sort
    together at the top of the per-item breakdown instead of being interleaved
    with real stores by price."""

    def _prices(self, rows):
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows", return_value=rows):
            return repository.get_item_competitor_prices("123")

    def test_synthetic_sources_lead_then_stores_cheapest_first(self):
        rows = [
            {"competitor_id": "s1", "competitor_name": "Steed", "source": "catalog",
             "url": "https://steed/x", "price": 744.99, "in_stock": True,
             "observed_at": "2026-07-30"},
            {"competitor_id": "gmb", "competitor_name": "Google benchmark",
             "source": "gmb_benchmark", "url": None, "price": 715.0,
             "in_stock": True, "observed_at": "2026-07-30"},
            {"competitor_id": "o1", "competitor_name": "Oak Bay", "source": "catalog",
             "url": "https://oakbay/x", "price": 680.0, "in_stock": True,
             "observed_at": "2026-07-30"},
            {"competitor_id": "gms", "competitor_name": "Google suggested",
             "source": "gmb_suggested", "url": None, "price": 699.0,
             "in_stock": True, "observed_at": "2026-07-30"},
        ]
        got = [r["competitor_id"] for r in self._prices(rows)]
        # Both Google rows first (cheapest of the two leading), then real stores.
        self.assertEqual(["gms", "gmb"], got[:2])
        self.assertEqual(["o1", "s1"], got[2:])


if __name__ == "__main__":
    unittest.main()
