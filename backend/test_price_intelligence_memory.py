"""Memory safety net for the 512MB worker (2026-09 OOMs).

Each nightly scrape used to leave ~75-100 MB resident, and the 4th-5th night's run
started from ~450 MB and was OOM-killed. These pin the pieces that stop that:

  1. The scheduler restarts a heavy worker before the run — at most once a day,
     never outside gunicorn (nothing would respawn it), never without a loop guard,
     and never on top of a just-booted worker's cache warm-ups.
  2. The Google benchmark phase writes as it goes and always closes its gRPC client.
  3. trim() never raises, even where there is no glibc.
  4. The sitemap iterator yields the same URLs after the decode-once change.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))

from app.services import memory_probe
from app.services.price_intelligence import (config, connectors, google_benchmark,
                                             repository, scrape_runner)


class TrimTests(unittest.TestCase):
    def test_returns_rss_pair_and_never_raises(self):
        before, after = memory_probe.trim("test")
        self.assertIsInstance(before, float)
        self.assertIsInstance(after, float)

    def test_missing_malloc_trim_is_a_noop(self):
        with patch.object(memory_probe, "_malloc_trim", return_value=None):
            memory_probe.trim("no glibc")

    def test_failing_malloc_trim_is_swallowed(self):
        with patch.object(memory_probe, "_malloc_trim",
                          return_value=MagicMock(side_effect=OSError("boom"))):
            self.assertEqual((0.0, 0.0), memory_probe.trim("broken"))


class PrescrapeRecycleTests(unittest.TestCase):
    DAY = "2026-09-30"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name, value in (("PRESCRAPE_RECYCLE_ENABLED", True),
                            ("PRESCRAPE_RECYCLE_MB", 300.0),
                            ("RECYCLE_MARKER_DIR", self.tmp.name)):
            p = patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.kill = patch.object(scrape_runner.os, "kill").start()
        self.addCleanup(patch.stopall)

    def _recycle(self, rss, gunicorn=True):
        with patch.object(memory_probe, "rss_mb", return_value=rss), \
             patch.object(scrape_runner, "_under_gunicorn", return_value=gunicorn):
            return scrape_runner._maybe_recycle_before_scrape(self.DAY)

    def _marker(self):
        return os.path.join(self.tmp.name, f"pi_recycle_{self.DAY}")

    def test_heavy_worker_restarts_itself_and_leaves_a_marker(self):
        self.assertTrue(self._recycle(453.1))
        self.kill.assert_called_once()
        self.assertTrue(os.path.exists(self._marker()))

    def test_at_most_once_per_day(self):
        self.assertTrue(self._recycle(453.1))
        # The respawned worker's boot warm-ups can exceed the threshold on their
        # own; the marker must stop it restarting again.
        self.assertFalse(self._recycle(320.0))
        self.kill.assert_called_once()

    def test_marker_is_per_day(self):
        self.assertTrue(self._recycle(453.1))
        with patch.object(self, "DAY", "2026-10-01"):
            self.assertTrue(self._recycle(453.1))
        self.assertEqual(2, self.kill.call_count)

    def test_light_worker_just_runs(self):
        self.assertFalse(self._recycle(212.0))
        self.assertFalse(self._recycle(300.0))  # at the threshold is not above it
        self.kill.assert_not_called()

    def test_unreadable_rss_never_restarts(self):
        self.assertFalse(self._recycle(0.0))
        self.kill.assert_not_called()

    def test_disabled(self):
        with patch.object(config, "PRESCRAPE_RECYCLE_ENABLED", False):
            self.assertFalse(self._recycle(453.1))
        self.kill.assert_not_called()

    def test_never_outside_gunicorn(self):
        # Under a bare uvicorn nothing respawns the worker: SIGTERM would take the
        # API down until someone noticed.
        self.assertFalse(self._recycle(453.1, gunicorn=False))
        self.kill.assert_not_called()
        self.assertFalse(os.path.exists(self._marker()))

    def test_no_marker_no_restart(self):
        with patch.object(config, "RECYCLE_MARKER_DIR",
                          os.path.join(self.tmp.name, "missing", "dir")):
            self.assertFalse(self._recycle(453.1))
        self.kill.assert_not_called()


class _StopLoop(BaseException):
    """Escapes the scheduler's `while True` (it catches Exception, not this)."""


class SchedulerLoopTests(unittest.TestCase):
    def _tick(self, *, uptime, recycled=False, blocking=False, status="idle"):
        settings_values = {"schedule_enabled": True, "schedule_timezone": "UTC",
                           "schedule_hour": 0, "schedule_minute": 0}
        with patch("app.services.price_intelligence.settings.get",
                   side_effect=settings_values.get), \
             patch.object(scrape_runner, "get_status", return_value={"status": status}), \
             patch.object(repository, "has_scheduler_blocking_run_on",
                          return_value=blocking), \
             patch.object(scrape_runner, "_PROCESS_STARTED", 1000.0), \
             patch.object(scrape_runner.time, "monotonic", return_value=1000.0 + uptime), \
             patch.object(scrape_runner, "_maybe_recycle_before_scrape",
                          return_value=recycled) as recycle, \
             patch.object(scrape_runner, "start_scrape") as start, \
             patch.object(scrape_runner.time, "sleep", side_effect=_StopLoop):
            with self.assertRaises(_StopLoop):
                scrape_runner._scheduler_loop()
        return recycle, start

    def test_fires_after_boot_settles(self):
        recycle, start = self._tick(uptime=config.SCHEDULER_BOOT_SETTLE_SECONDS + 1)
        recycle.assert_called_once()
        start.assert_called_once_with(trigger="scheduled")

    def test_waits_out_boot_warmups(self):
        recycle, start = self._tick(uptime=30)
        recycle.assert_not_called()
        start.assert_not_called()

    def test_recycling_worker_does_not_start_the_run(self):
        _recycle, start = self._tick(uptime=10_000, recycled=True)
        start.assert_not_called()

    def test_never_recycles_when_the_day_is_already_done(self):
        recycle, start = self._tick(uptime=10_000, blocking=True)
        recycle.assert_not_called()
        start.assert_not_called()


class _Pager:
    """client.search(...) result: iterable of rows with the view attribute set."""

    def __init__(self, views, attr):
        self._rows = [MagicMock(**{attr: v}) for v in views]

    def __iter__(self):
        return iter(self._rows)


def _view(variant_id, price_attr, amount=10.0):
    view = MagicMock(spec=["offer_id", "id", "title", price_attr])
    view.offer_id = f"shopify_CA_1_{variant_id}"
    view.id = ""
    view.title = "t"
    setattr(view, price_attr, MagicMock(amount_micros=int(amount * 1e6),
                                        currency_code="CAD"))
    return view


class BenchmarkStreamingTests(unittest.TestCase):
    OFFER_MAP = [{"variant_id": str(v), "sku": f"s{v}", "item_id": str(v)}
                 for v in range(1, 8)]

    def setUp(self):
        self.client = MagicMock()
        bench = [_view(v, "benchmark_price") for v in range(1, 6)]     # 5 rows
        sugg = [_view(v, "suggested_price") for v in range(1, 4)]      # 3 rows

        def search(_client, query):
            if "price_competitiveness" in query:
                return _Pager(bench, "price_competitiveness_product_view")
            return _Pager(sugg, "price_insights_product_view")

        for target, attr, value in (
                (google_benchmark, "_report_client", MagicMock(return_value=self.client)),
                (google_benchmark, "_search", search),
                (google_benchmark, "ensure_competitors", MagicMock()),
                (repository, "get_google_offer_map", MagicMock(return_value=self.OFFER_MAP)),
                (config, "FLUSH_ROWS", 2),
                (config, "GOOGLE_INSIGHTS_ENABLED", True)):
            p = patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        self.load = patch.object(repository, "load_rows").start()
        self.marked = patch.object(repository, "mark_competitor_scraped").start()
        self.addCleanup(patch.stopall)

    def test_writes_in_flush_sized_chunks(self):
        stats = google_benchmark.run_benchmark_sync("run", "2026-09-30T09:30:00Z")
        sizes = [len(call.args[1]) for call in self.load.call_args_list]
        # 5 benchmark rows -> 2+2+1, 3 suggested rows -> 2+1; never all at once.
        self.assertEqual([2, 2, 1, 2, 1], sizes)
        self.assertEqual(8, stats["observations"])
        self.assertEqual(5, stats["benchmark"]["written"])
        self.assertEqual(3, stats["suggested"]["written"])
        self.assertEqual(2, self.marked.call_count)
        self.client.transport.close.assert_called_once()

    def test_closes_the_client_when_a_pull_fails(self):
        self.load.side_effect = RuntimeError("bq down")
        with self.assertRaises(RuntimeError):
            google_benchmark.run_benchmark_sync("run", "2026-09-30T09:30:00Z")
        self.client.transport.close.assert_called_once()

    def test_a_failing_close_does_not_fail_the_run(self):
        self.client.transport.close.side_effect = RuntimeError("channel gone")
        stats = google_benchmark.run_benchmark_sync("run", "2026-09-30T09:30:00Z")
        self.assertEqual(8, stats["observations"])

    def test_dry_run_counts_without_writing(self):
        stats = google_benchmark.run_benchmark_sync(
            "run", "2026-09-30T09:30:00Z", dry_run=True)
        self.load.assert_not_called()
        self.marked.assert_not_called()
        google_benchmark.ensure_competitors.assert_not_called()
        self.assertTrue(stats["dry_run"])
        self.assertEqual(8, stats["observations"])
        self.client.transport.close.assert_called_once()


class SitemapIteratorTests(unittest.TestCase):
    INDEX = ("<?xml version='1.0'?><sitemapindex>"
             "<sitemap><loc>https://s.com/sitemap_pages.xml</loc></sitemap>"
             "<sitemap><loc>https://s.com/sitemap_products_1.xml</loc></sitemap>"
             "</sitemapindex>")
    PRODUCTS = ("<?xml version='1.0'?><urlset>"
                "<url><loc>https://s.com/products/a</loc></url>"
                "<url><loc> https://s.com/products/b </loc></url>"
                "<url><loc>https://s.com/products/a</loc></url>"
                "</urlset>")
    PAGES = ("<?xml version='1.0'?><urlset>"
             "<url><loc>https://s.com/about</loc></url>"
             "<url><loc>https://s.com/products/b</loc></url></urlset>")

    def _resp(self, text, status=200):
        return MagicMock(status_code=status, text=text)

    def test_index_children_are_followed_products_first_and_deduped(self):
        conn = connectors.GenericSitemapConnector("https://s.com")
        bodies = {
            "https://s.com/sitemap.xml": self._resp(self.INDEX),
            "https://s.com/sitemap_products_1.xml": self._resp(self.PRODUCTS),
            "https://s.com/sitemap_pages.xml": self._resp(self.PAGES),
        }
        with patch.object(conn, "_sitemap_sources",
                          return_value=["https://s.com/sitemap.xml"]), \
             patch.object(conn, "_get", side_effect=lambda url, **kw: bodies.get(url)):
            urls = list(conn._iter_page_urls())
        self.assertEqual(["https://s.com/products/a", "https://s.com/products/b",
                          "https://s.com/about"], urls)

    def test_non_xml_and_failed_fetches_are_skipped(self):
        conn = connectors.GenericSitemapConnector("https://s.com")
        bodies = {
            "https://s.com/a.xml": self._resp("not xml at all " * 20),
            "https://s.com/b.xml": self._resp(self.PRODUCTS, status=404),
            "https://s.com/c.xml": self._resp(self.PAGES),
        }
        with patch.object(conn, "_sitemap_sources",
                          return_value=list(bodies)), \
             patch.object(conn, "_get", side_effect=lambda url, **kw: bodies.get(url)):
            urls = list(conn._iter_page_urls())
        self.assertEqual(["https://s.com/about", "https://s.com/products/b"], urls)


class SingleParseTests(unittest.TestCase):
    """page_listings() parses once but must return what the old two-parse path
    (Magento GraphQL probe, else static extraction) returned."""

    FIXTURES = os.path.join(os.path.dirname(__file__), "tests", "fixtures")

    def test_matches_static_extraction_on_fixture_pages(self):
        with patch.object(connectors, "polite_get", return_value=None):
            for name in sorted(os.listdir(self.FIXTURES)):
                if not name.endswith(".html"):
                    continue
                with open(os.path.join(self.FIXTURES, name), encoding="utf-8") as fh:
                    html = fh.read()
                url = f"https://www.example.com/p/{name}"
                connectors._magento_graphql_capabilities.clear()
                with self.subTest(fixture=name):
                    listings = connectors.page_listings(html, url)
                    self.assertTrue(listings)
                    self.assertEqual(connectors.extract_listings(html, url), listings)
        connectors._magento_graphql_capabilities.clear()


if __name__ == "__main__":
    unittest.main()
