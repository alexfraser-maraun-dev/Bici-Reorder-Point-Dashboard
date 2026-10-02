"""Stale competitor prices must not outlive fresh ones.

Two live failures (2026-10-02), both showing $133.95 against a real $142.95:
  * Steed, GP5000 S TR 32mm: tonight's row was out of stock, and the per-store pick
    ranked in-stock ahead of recency, so a weeks-old in-stock row from another
    diff_key won. Every per-store pick now reads only the store's latest run
    (`sql_store_fresh`) before applying in-stock-first.
  * Primeau, 30mm: a confirmed link was skipped by the URL re-check on every
    full-scan night because its competitor's catalog was "crawled", yet the crawl
    hunts only unlinked items, so it never reached the page. The re-check now skips
    only the (item, competitor) pairs the crawl actually priced.

Live BigQuery isn't exercised (same posture as test_price_intelligence_matrix):
_rows is mocked and the emitted SQL is asserted.
"""
import unittest
from unittest.mock import patch

from app.services.price_intelligence import repository
from app.services.price_intelligence.scrape_runner import _links_to_recheck


def _capture(fn, *args, **kwargs):
    repository._caches.clear()
    captured = []
    with patch.object(repository, "ensure_pi_tables"), \
         patch.object(repository, "_rows",
                      side_effect=lambda query, params=None: captured.append(query) or []):
        fn(*args, **kwargs)
    return "\n".join(captured)


class StoreRepFreshnessTests(unittest.TestCase):
    def test_helper_keeps_only_the_stores_latest_run(self):
        sql = repository.sql_store_fresh()
        self.assertIn("MAX(observed_at) OVER (PARTITION BY match_item_id, "
                      f"{repository.SQL_STORE_KEY})", sql)
        self.assertIn(f"INTERVAL {repository.STORE_REP_FRESH_HOURS} HOUR", sql)

    def test_every_per_store_pick_reads_the_fresh_rows(self):
        cases = {
            "tracked market": (repository.get_tracked_products_with_market, (7,)),
            "matrix market": (repository.get_tracked_matrices_with_market, (7,)),
            "matrix coverage": (repository.get_matrix_coverage, ("6182",)),
            "item breakdown": (repository.get_item_competitor_prices, ("65149",)),
        }
        for name, (fn, args) in cases.items():
            with self.subTest(name):
                sql = _capture(fn, *args)
                fresh_cte = f"fresh AS ({repository.sql_store_fresh()})"
                self.assertIn(fresh_cte, sql)
                self.assertNotIn("FROM latest", sql.split(fresh_cte, 1)[1],
                                 "a per-store pick bypasses the freshness filter")

    def test_breakdown_still_prefers_in_stock_within_a_run(self):
        # Two listings at one store from the same run: the in-stock one wins.
        rows = [
            {"competitor_id": "s1", "source": "catalog", "url": "https://steed/a",
             "price": 139.95, "in_stock": False, "observed_at": "2026-10-02T09:00:00Z"},
            {"competitor_id": "s1", "source": "link", "url": "https://steed/b",
             "price": 142.95, "in_stock": True, "observed_at": "2026-10-02T09:05:00Z"},
        ]
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows", return_value=rows):
            got = repository.get_item_competitor_prices("65149")
        self.assertEqual([142.95], [r["price"] for r in got])


class LinkRecheckTests(unittest.TestCase):
    ITEMS = {"65148": {}, "65149": {}}

    def _link(self, item_id, cid, url):
        return {"link_id": f"{item_id}-{cid}", "item_id": item_id,
                "competitor_id": cid, "competitor_url": url}

    def test_link_at_a_crawled_store_is_rechecked_unless_the_crawl_priced_it(self):
        primeau = self._link("65148", "primeau", "https://primeau/gp5000")
        steed = self._link("65149", "steed", "https://steed/gp5000")
        # Both stores were crawled tonight; only Steed's pair got a crawl price.
        got = _links_to_recheck([primeau, steed], self.ITEMS, [],
                                crawl_refreshed={("65149", "steed")})
        self.assertEqual([primeau], got)

    def test_targeted_night_rechecks_every_link(self):
        links = [self._link("65148", "primeau", "https://primeau/gp5000"),
                 self._link("65149", "steed", "https://steed/gp5000")]
        self.assertEqual(links, _links_to_recheck(links, self.ITEMS, [], set()))

    def test_tracked_url_for_the_same_item_is_not_fetched_twice(self):
        link = self._link("65148", "steed", "https://steed/gp5000")
        urls = [{"url": "https://steed/gp5000", "item_id": "65148"}]
        self.assertEqual([], _links_to_recheck([link], self.ITEMS, urls, set()))

    def test_untracked_item_is_skipped(self):
        link = self._link("99999", "steed", "https://steed/x")
        self.assertEqual([], _links_to_recheck([link], self.ITEMS, [], set()))


if __name__ == "__main__":
    unittest.main()
