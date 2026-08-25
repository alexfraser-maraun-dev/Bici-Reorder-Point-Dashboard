import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

from app.services.price_intelligence.connectors import (
    PageScraper, _to_price, extract_listings, parse_product_page, resolve_listing,
)


FIXTURE = os.path.join(os.path.dirname(__file__), "tests", "fixtures", "primeau_supersix.html")
URL = "https://www.primeauvelo.com/en_ca/supersix-evo-2-0058217"


class PrimeauMagentoExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIXTURE, encoding="utf-8") as fh:
            cls.html = fh.read()

    def test_extracts_all_sixteen_children(self):
        listings = extract_listings(self.html, URL)
        self.assertEqual(16, len(listings))
        self.assertTrue(all(r["price_scope"] == "variant" for r in listings))
        self.assertTrue(all(r["extraction_method"] == "magento_json_config" for r in listings))

    def test_resolves_marked_down_blue_48_by_sku(self):
        result = resolve_listing(extract_listings(self.html, URL), {"sku": "0058217001"})
        row = result["listing"]
        self.assertEqual("exact", result["status"])
        self.assertEqual(["Blue", "48cm"], row["variant_options"])
        self.assertEqual(7054.93, row["price"])
        self.assertEqual(8299.0, row["compare_at_price"])
        self.assertEqual("196870280243", row["gtin"])
        self.assertTrue(row["in_stock"])

    def test_resolves_full_price_blue_52_by_sku(self):
        row = resolve_listing(extract_listings(self.html, URL), {"sku": "0058217003"})["listing"]
        self.assertEqual(["Blue", "52cm"], row["variant_options"])
        self.assertEqual(8299.0, row["price"])
        self.assertIsNone(row["compare_at_price"])

    def test_jsonld_offer_order_does_not_change_magento_results(self):
        reordered = self.html.replace(
            '"sku":"0058217003","price":"8299.00"',
            '"sku":"0058217001","price":"7054.93"', 1,
        )
        original = {r["sku"]: r["price"] for r in extract_listings(self.html, URL)}
        changed = {r["sku"]: r["price"] for r in extract_listings(reordered, URL)}
        self.assertEqual(original, changed)


class GenericStructuredDataTests(unittest.TestCase):
    def test_single_offer_is_product_scope(self):
        html = '''<script type="application/ld+json">{"@type":"Product","name":"Pump",
        "sku":"P1","offers":{"@type":"Offer","price":"39.99","priceCurrency":"CAD"}}</script>'''
        row = parse_product_page(html, URL)
        self.assertEqual("product", row["price_scope"])
        self.assertEqual("P1", row["sku"])

    def test_multiple_offers_are_not_reduced_to_first(self):
        html = '''<script type="application/ld+json">{"@type":"Product","name":"Bike",
        "offers":[{"@type":"Offer","sku":"B","price":"20","priceCurrency":"CAD"},
        {"@type":"Offer","sku":"A","price":"10","priceCurrency":"CAD"}]}</script>'''
        listings = extract_listings(html, URL)
        self.assertEqual({"A", "B"}, {r["sku"] for r in listings})
        self.assertEqual(10, resolve_listing(listings, {"sku": "A"})["listing"]["price"])
        unresolved = resolve_listing(listings)
        self.assertEqual("ambiguous", unresolved["status"])
        self.assertEqual((10, 20), (unresolved["listing"]["price_low"], unresolved["listing"]["price_high"]))

    def test_product_group_variants_and_gtin(self):
        html = '''<script type="application/ld+json">{"@type":"ProductGroup","name":"Jersey",
        "hasVariant":[{"@type":"Product","name":"Jersey Blue M","sku":"JM","color":"Blue","size":"M",
        "gtin13":"1234567890123","offers":{"@type":"Offer","price":"99.95","availability":"https://schema.org/OutOfStock"}}]}</script>'''
        row = resolve_listing(extract_listings(html, URL), {"gtin": "1234567890123"})["listing"]
        self.assertEqual(["Blue", "M"], row["variant_options"])
        self.assertFalse(row["in_stock"])

    def test_product_group_graph_duplicates_are_collapsed(self):
        child = {"@type": "Product", "name": "Jersey M", "sku": "JM",
                 "offers": {"@type": "Offer", "price": "99.95"}}
        import json
        html = '<script type="application/ld+json">' + json.dumps({
            "@graph": [{"@type": "ProductGroup", "name": "Jersey", "hasVariant": [child]}, child]
        }) + '</script>'
        self.assertEqual(1, len(extract_listings(html, URL)))

    def test_aggregate_offer_is_range(self):
        html = '''<script type="application/ld+json">{"@type":"Product","name":"Helmet",
        "offers":{"@type":"AggregateOffer","lowPrice":"129.99","highPrice":"179.99","priceCurrency":"CAD"}}</script>'''
        row = parse_product_page(html, URL)
        self.assertEqual("range", row["price_scope"])
        self.assertEqual(129.99, row["price_low"])
        self.assertEqual(179.99, row["price_high"])

    def test_malformed_jsonld_falls_back_to_microdata(self):
        html = '<script type="application/ld+json">{bad</script><span itemprop="price" content="49.95"></span>'
        self.assertEqual(49.95, parse_product_page(html, URL)["price"])

    def test_localized_prices(self):
        self.assertEqual(1234.56, _to_price("1 234,56 $"))
        self.assertEqual(1234.56, _to_price("$1,234.56"))

    def test_unique_color_size_resolution(self):
        listings = [
            {"price": 10, "variant_options": ["Blue", "M"], "sku": "BM"},
            {"price": 20, "variant_options": ["Blue", "L"], "sku": "BL"},
        ]
        result = resolve_listing(listings, {"variant_options": ["Blue", "M", "Unisex"]})
        self.assertEqual("BM", result["listing"]["sku"])


class ShopifyRegressionTests(unittest.TestCase):
    def test_js_endpoint_still_resolves_exact_url_variant(self):
        class Response:
            status_code = 200
            def json(self):
                return {"title": "Shoe", "vendor": "Brand", "currency": "CAD", "variants": [
                    {"id": 1, "sku": "S1", "barcode": "111", "title": "40",
                     "option1": "40", "price": 10000, "compare_at_price": 12000, "available": True},
                    {"id": 2, "sku": "S2", "barcode": "222", "title": "41",
                     "option1": "41", "price": 11000, "compare_at_price": None, "available": False},
                ]}
        with patch("app.services.price_intelligence.connectors.polite_get", return_value=Response()):
            row = PageScraper().fetch("https://shop.example/products/shoe?variant=2")
        self.assertEqual("S2", row["sku"])
        self.assertEqual(110.0, row["price"])
        self.assertFalse(row["in_stock"])
        self.assertEqual("shopify_js", row["extraction_method"])


class DecisionSafetyTests(unittest.TestCase):
    def test_range_observation_never_generates_events(self):
        from app.services.price_intelligence.scrape_runner import _build_events
        obs = {
            "diff_key": "range", "run_id": "r", "observed_at": "2026-01-01T00:00:00Z",
            "source": "url", "price": 10, "price_scope": "range",
            "match_item_id": "1",
        }
        self.assertEqual([], _build_events({}, obs, "Store", {"1": {"current_retail": 20}}))

    def test_market_queries_are_item_specific_and_exclude_ranges(self):
        from app.services.price_intelligence import repository
        repository._caches.clear()
        captured = []
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows", side_effect=lambda query, params=None: captured.append(query) or []):
            repository.get_tracked_products_with_market(days=7)
            repository.get_item_competitor_prices("1", days=7)
        sql = "\n".join(captured)
        self.assertIn("o.match_item_id = @item_id", sql)
        self.assertIn("price_scope", sql)
        self.assertIn("item_matrix_id IS NOT NULL", sql)
        self.assertNotIn("matrix_market", sql)


if __name__ == "__main__":
    unittest.main()


class LocaleConfinementTests(unittest.TestCase):
    """Multi-geo crawls stay English + Canadian: non-English locales are never
    fetched, other-geo English duplicates are dropped when CA/neutral exist,
    and brand slugs that merely look like language codes are never filtered."""

    def test_non_english_path_segments_are_filtered(self):
        from app.services.price_intelligence.connectors import NON_ENGLISH_PATH_RE
        for path in ("/fr/velo-route", "/fr-ca/products/x", "/de/rennrad",
                     "/en/fr/x", "/zh-cn/bikes"):
            self.assertTrue(NON_ENGLISH_PATH_RE.search(path), path)

    def test_brand_slugs_are_not_mistaken_for_languages(self):
        from app.services.price_intelligence.connectors import NON_ENGLISH_PATH_RE
        for path in ("/products/de-rosa-frame", "/no-tubes-sealant",
                     "/products/fresh-frame", "/en-ca/products/de-rosa",
                     "/it-clips-here/x", "/pt-cruiser-parts"):
            self.assertFalse(NON_ENGLISH_PATH_RE.search(path), path)

    def test_prefer_ca_english_drops_other_geos_only_when_ca_exists(self):
        from app.services.price_intelligence.connectors import prefer_ca_english
        mixed = [
            "https://s.com/en-us/products/a",
            "https://s.com/en-ca/products/a",
            "https://s.com/products/b",
        ]
        self.assertEqual(
            ["https://s.com/en-ca/products/a", "https://s.com/products/b"],
            prefer_ca_english(mixed))
        # A store publishing ONLY under /en-us/ still gets crawled.
        us_only = ["https://s.com/en-us/products/a", "https://s.com/en-us/products/b"]
        self.assertEqual(us_only, prefer_ca_english(us_only))

    def test_sitemap_sources_prefer_english_canadian(self):
        from app.services.price_intelligence import connectors

        class Robots:
            status_code = 200
            text = ("Sitemap: https://s.com/sitemap_products_fr-ca.xml\n"
                    "Sitemap: https://s.com/sitemap_products_en-us.xml\n"
                    "Sitemap: https://s.com/sitemap_products_en-ca.xml\n")

        conn = connectors.GenericSitemapConnector("https://s.com")
        with patch.object(connectors, "polite_get", return_value=Robots()):
            maps = conn._sitemap_sources()
        self.assertEqual("https://s.com/sitemap_products_en-ca.xml", maps[0])
        self.assertNotIn("https://s.com/sitemap_products_fr-ca.xml", maps)
        self.assertNotIn("https://s.com/sitemap_products_en-us.xml", maps)

    def test_candidate_page_urls_confine_locale(self):
        from app.services.price_intelligence import connectors
        conn = connectors.GenericSitemapConnector("https://s.com")
        pages = [
            "https://s.com/fr-ca/produits/velo",
            "https://s.com/en-us/products/bike",
            "https://s.com/en-ca/products/bike",
            "https://s.com/products/de-rosa-frame",
            "https://s.com/blogs/news",
        ]
        with patch.object(conn, "_iter_page_urls", return_value=iter(pages)):
            out = conn._candidate_page_urls()
        self.assertEqual(
            ["https://s.com/en-ca/products/bike",
             "https://s.com/products/de-rosa-frame"],
            out)


class CrawlRotationTests(unittest.TestCase):
    """Cursor rotation: catalogs bigger than the nightly cap are covered over
    successive nights instead of the same first slice being re-crawled forever."""

    def _json_resp(self, products):
        class R:
            status_code = 200
            def __init__(self, payload):
                self._payload = payload
            def json(self):
                return self._payload
        return R({"products": products})

    def test_shopify_json_resumes_at_start_page_and_reports_cap_hit(self):
        from app.services.price_intelligence import connectors
        product = {"title": "Bike", "vendor": "X", "handle": "bike",
                   "variants": [{"id": 1, "title": "Default Title", "price": "9.99"}]}
        requested = []

        def fake_get(url, **kw):
            requested.append(url)
            return self._json_resp([product])  # never-empty catalog → cap hit

        conn = connectors.ShopifyJsonConnector("https://s.com", start_page=3)
        with patch.object(connectors, "polite_get", side_effect=fake_get), \
             patch.object(connectors.config, "MAX_CATALOG_PAGES", 2):
            list(conn.iter_products())
        self.assertEqual(["https://s.com/products.json?limit=250&page=3",
                          "https://s.com/products.json?limit=250&page=4"], requested)
        self.assertTrue(conn.cap_hit)
        self.assertEqual(5, conn.next_cursor)  # resume at page 5 tomorrow
        self.assertEqual(2, conn.pages_done)
        self.assertEqual(2, conn.products_seen)

    def test_shopify_json_wraps_cursor_when_catalog_ends(self):
        from app.services.price_intelligence import connectors
        product = {"title": "Bike", "vendor": "X", "handle": "bike",
                   "variants": [{"id": 1, "title": "Default Title", "price": "9.99"}]}

        def fake_get(url, **kw):
            page = int(url.rsplit("page=", 1)[1])
            return self._json_resp([product] if page <= 3 else [])

        conn = connectors.ShopifyJsonConnector("https://s.com", start_page=3)
        with patch.object(connectors, "polite_get", side_effect=fake_get), \
             patch.object(connectors.config, "MAX_CATALOG_PAGES", 10):
            list(conn.iter_products())
        self.assertFalse(conn.cap_hit)
        self.assertEqual(1, conn.next_cursor)  # catalog ended — wrap to front

    def test_shopify_json_restarts_when_catalog_shrank_below_cursor(self):
        from app.services.price_intelligence import connectors
        product = {"title": "Bike", "vendor": "X", "handle": "bike",
                   "variants": [{"id": 1, "title": "Default Title", "price": "9.99"}]}

        def fake_get(url, **kw):
            page = int(url.rsplit("page=", 1)[1])
            return self._json_resp([product] if page <= 2 else [])

        conn = connectors.ShopifyJsonConnector("https://s.com", start_page=8)
        with patch.object(connectors, "polite_get", side_effect=fake_get), \
             patch.object(connectors.config, "MAX_CATALOG_PAGES", 10):
            n = len(list(conn.iter_products()))
        self.assertEqual(2, n)          # restarted from page 1 tonight
        self.assertEqual(1, conn.next_cursor)

    def test_html_crawl_rotates_sorted_candidates(self):
        from app.services.price_intelligence import connectors
        conn = connectors.GenericSitemapConnector("https://s.com", start_offset=3)
        # Unsorted input: rotation must apply to the SORTED list for a stable
        # cross-night order.
        pages = [f"https://s.com/products/x-{i}" for i in (4, 0, 2, 1, 3)]
        fetched = []

        def fake_get(url, **kw):
            fetched.append(url)
            class R:
                status_code = 200
                text = "<html></html>"  # parses to zero listings; fetch still counts
            return R()

        with patch.object(conn, "_candidate_page_urls", return_value=pages), \
             patch.object(connectors, "polite_get", side_effect=fake_get), \
             patch.object(connectors.config, "MAX_HTML_PRODUCT_PAGES", 2):
            list(conn.iter_products())
        self.assertEqual(["https://s.com/products/x-3",
                          "https://s.com/products/x-4"], fetched)
        self.assertTrue(conn.cap_hit)
        self.assertEqual(0, conn.next_cursor)  # (3 + 2) % 5

    def test_html_crawl_finishing_list_resets_cursor(self):
        from app.services.price_intelligence import connectors
        conn = connectors.GenericSitemapConnector("https://s.com", start_offset=1)
        pages = [f"https://s.com/products/x-{i}" for i in range(3)]

        def fake_get(url, **kw):
            class R:
                status_code = 200
                text = "<html></html>"
            return R()

        with patch.object(conn, "_candidate_page_urls", return_value=pages), \
             patch.object(connectors, "polite_get", side_effect=fake_get), \
             patch.object(connectors.config, "MAX_HTML_PRODUCT_PAGES", 300):
            list(conn.iter_products())
        self.assertFalse(conn.cap_hit)
        self.assertEqual(0, conn.next_cursor)
        self.assertEqual(3, conn.pages_done)


def _ok_page(fetched=None):
    """A 200 that parses to zero listings — the fetch still counts."""
    def fake_get(url, **kw):
        if fetched is not None:
            fetched.append(url)

        class R:
            status_code = 200
            text = "<html></html>"
        return R()
    return fake_get


class SlugTokenTests(unittest.TestCase):
    """Model tokens are what promote a URL past the alphabetical sweep, so the
    stoplist matters: score on 'bike' or 'black' and every page ranks."""

    def test_generic_vocabulary_and_years_are_dropped(self):
        from app.services.price_intelligence.connectors import _slug_tokens
        tokens = _slug_tokens("2024 Matte Black Carbon Road Bike Medium")
        self.assertEqual(set(), tokens)

    def test_model_codes_survive(self):
        from app.services.price_intelligence.connectors import _slug_tokens
        self.assertEqual({"gp5000"}, _slug_tokens("Continental GP5000 700x25 Tire")
                         - {"continental", "700x25"})
        self.assertIn("soloist", _slug_tokens("Cervelo Soloist Carbon Road Bike"))
        self.assertIn("aeroad", _slug_tokens("aeroad-cf-slx"))

    def test_short_numbers_are_dropped_but_long_ones_kept(self):
        from app.services.price_intelligence.connectors import _slug_tokens
        self.assertNotIn("54", _slug_tokens("Frame 54"))       # a size
        self.assertNotIn("2", _slug_tokens("supersix-evo-2-0058217"))
        self.assertIn("0058217", _slug_tokens("supersix-evo-2-0058217"))  # a product id


class CrawlTargetingTests(unittest.TestCase):
    """The nightly page budget goes to pages that might be the items we still
    can't price, instead of to whatever sorts first alphabetically."""

    ITEMS = {
        "1": {"brand": "Cervelo", "title": "Cervelo Soloist Carbon Road Bike"},
        "2": {"brand": "Cervelo", "title": "Cervelo Caledonia 5"},
        # "Advanced" is a frame-tier word across Giant's whole range, so it is
        # stoplisted; "tcr" and "propel" are what actually name the model.
        "3": {"brand": "Giant", "title": "Giant TCR Propel Advanced"},
    }

    def test_groupset_and_tier_vocabulary_is_not_a_model_signal(self):
        """These read like model names but sit on hundreds of accessory pages —
        they buried the real models in the first dry-run against live sitemaps."""
        from app.services.price_intelligence.connectors import CrawlTargets
        targets = CrawlTargets.from_items({
            "1": {"brand": "SRAM", "title": "SRAM Force AXS Power Meter"},
            "2": {"brand": "Wahoo", "title": "Wahoo Kickr Core 2 with Zwift Cog and Click"},
            "3": {"brand": "Shimano", "title": "Shimano Dura-Ace Di2 Groupset"},
        })
        for noise in ("with", "and", "power", "meter", "axs", "di2", "dura", "ace",
                      "force", "core"):
            self.assertNotIn(noise, targets.model_tokens, noise)
        self.assertIn("kickr", targets.model_tokens)
        self.assertIn("zwift", targets.model_tokens)

    def test_brand_names_never_become_model_tokens(self):
        from app.services.price_intelligence.connectors import CrawlTargets
        targets = CrawlTargets.from_items(self.ITEMS)
        self.assertIn("soloist", targets.model_tokens)
        self.assertNotIn("cervelo", targets.model_tokens)
        self.assertNotIn("giant", targets.model_tokens)

    def test_linked_items_stop_being_hunted(self):
        from app.services.price_intelligence.connectors import ItemTokenIndex
        index = ItemTokenIndex(self.ITEMS)
        # Item 1 already has a confirmed link on this competitor.
        targets = index.targets_for({"2", "3"})
        self.assertNotIn("soloist", targets.model_tokens)
        self.assertIn("caledonia", targets.model_tokens)

    def test_model_hit_outranks_brand_only(self):
        from app.services.price_intelligence.connectors import CrawlTargets
        targets = CrawlTargets.from_items(self.ITEMS)
        self.assertGreater(targets.score("/products/cervelo-soloist-2024"),
                           targets.score("/products/cervelo-r5-frameset"))

    def test_targeted_pages_are_crawled_before_the_sweep(self):
        from app.services.price_intelligence import connectors
        targets = connectors.CrawlTargets.from_items(self.ITEMS)
        conn = connectors.GenericSitemapConnector("https://s.com", brand_tokens=targets,
                                                  start_offset=0)
        pages = [
            "https://s.com/products/cervelo-aaa-frameset",   # brand only
            "https://s.com/products/cervelo-bbb-frameset",   # brand only
            "https://s.com/products/cervelo-soloist-2024",   # brand + 1 model token
            "https://s.com/products/giant-tcr-propel",       # brand + 2 model tokens
        ]
        fetched = []
        with patch.object(conn, "_candidate_page_urls", return_value=pages), \
             patch.object(connectors, "polite_get", side_effect=_ok_page(fetched)), \
             patch.object(connectors.config, "MAX_HTML_PRODUCT_PAGES", 10):
            list(conn.iter_products())
        # Both model-matching pages come before either brand-only page. Within
        # the head, more model tokens wins: "giant-tcr-propel" matches two.
        self.assertEqual({"https://s.com/products/cervelo-soloist-2024",
                          "https://s.com/products/giant-tcr-propel"}, set(fetched[:2]))
        self.assertEqual("https://s.com/products/giant-tcr-propel", fetched[0])
        self.assertEqual(2, conn.targeted_candidates)
        self.assertEqual(2, conn.targeted_pages_done)

    def test_house_vocabulary_stops_being_hunted(self):
        """A token on a large share of one store's catalog can't distinguish
        anything there, however good a model name it is elsewhere."""
        from app.services.price_intelligence import connectors
        targets = connectors.CrawlTargets(brand_names=["Cervelo"],
                                          model_tokens={"soloist", "caledonia"})
        conn = connectors.GenericSitemapConnector("https://s.com", brand_tokens=targets)
        # "soloist" is on this store's whole catalog; "caledonia" on one page.
        pages = ([f"https://s.com/products/cervelo-soloist-{i}" for i in range(200)]
                 + ["https://s.com/products/cervelo-caledonia-5"])
        head, tail = conn._rank_candidates(pages)
        self.assertEqual(["soloist"], conn.common_tokens_dropped)
        self.assertEqual(["https://s.com/products/cervelo-caledonia-5"], head)
        self.assertEqual(200, len(tail))

    def test_brand_only_candidates_stay_in_the_rotating_tail(self):
        """A brand hit must not promote into the head: with the gate on, every
        candidate has one, and a head-only crawl would freeze the cursor."""
        from app.services.price_intelligence import connectors
        targets = connectors.CrawlTargets(brand_names=["Cervelo"])  # no model tokens
        conn = connectors.GenericSitemapConnector("https://s.com", brand_tokens=targets,
                                                  start_offset=3)
        pages = [f"https://s.com/products/cervelo-x-{i}" for i in (4, 0, 2, 1, 3)]
        fetched = []
        with patch.object(conn, "_candidate_page_urls", return_value=pages), \
             patch.object(connectors, "polite_get", side_effect=_ok_page(fetched)), \
             patch.object(connectors.config, "MAX_HTML_PRODUCT_PAGES", 2):
            list(conn.iter_products())
        self.assertEqual(0, conn.targeted_candidates)
        self.assertEqual(["https://s.com/products/cervelo-x-3",
                          "https://s.com/products/cervelo-x-4"], fetched)
        self.assertEqual(0, conn.next_cursor)  # (3 + 2) % 5 — rotation intact

    def test_head_does_not_starve_the_sweep(self):
        from app.services.price_intelligence import connectors
        targets = connectors.CrawlTargets(brand_names=["Cervelo"],
                                          model_tokens={"soloist"})
        conn = connectors.GenericSitemapConnector("https://s.com", brand_tokens=targets)
        pages = ([f"https://s.com/products/cervelo-soloist-{i}" for i in range(10)]
                 + ["https://s.com/products/cervelo-zzz-frameset"])
        fetched = []
        with patch.object(conn, "_candidate_page_urls", return_value=pages), \
             patch.object(connectors, "polite_get", side_effect=_ok_page(fetched)), \
             patch.object(connectors.config, "MAX_HTML_PRODUCT_PAGES", 5):
            list(conn.iter_products())
        # The head takes its share of the budget; whatever is left still sweeps
        # the tail, however much the head wanted (10 candidates for 5 pages here).
        head_share = connectors._HtmlPageCrawler.HEAD_BUDGET_SHARE
        self.assertEqual(int(5 * head_share), conn.targeted_pages_done)
        self.assertLess(conn.targeted_pages_done, 5, "the tail is never starved")
        self.assertIn("https://s.com/products/cervelo-zzz-frameset", fetched)
        self.assertTrue(conn.cap_hit)  # head was truncated: coverage incomplete


class BrandGateTests(unittest.TestCase):
    """The brand filter used to be a hard drop, which silently discarded the
    entire catalog of any store that doesn't put brands in its URLs."""

    def _conn(self, pages, brands=("Cervelo",), settings=None):
        from app.services.price_intelligence import connectors
        targets = connectors.CrawlTargets(brand_names=list(brands))
        conn = connectors.GenericSitemapConnector("https://s.com", brand_tokens=targets,
                                                  settings=settings)
        return conn, conn._collect_candidates(iter(pages))

    def test_brandless_urls_are_kept_instead_of_zeroing_the_crawl(self):
        pages = [f"https://s.com/product/{1000 + i}-carbon-wheelset" for i in range(50)]
        conn, out = self._conn(pages)
        self.assertEqual(50, len(out))          # was: 0 candidates, "no products"
        self.assertFalse(conn.brand_gate_applied)
        self.assertEqual(0.0, conn.brand_hit_rate)

    def test_brand_rich_catalog_still_gets_filtered(self):
        pages = ([f"https://s.com/products/cervelo-{i}" for i in range(30)]
                 + [f"https://s.com/products/other-{i}" for i in range(30)])
        conn, out = self._conn(pages)
        self.assertEqual(30, len(out))
        self.assertTrue(conn.brand_gate_applied)
        self.assertEqual(0.5, conn.brand_hit_rate)

    def test_brand_filter_can_be_forced_on_per_competitor(self):
        from app.services.price_intelligence.connectors import CrawlSettings
        pages = [f"https://s.com/product/{1000 + i}-carbon-wheelset" for i in range(50)]
        conn, out = self._conn(pages, settings=CrawlSettings({"brand_filter": "on"}))
        self.assertEqual([], out)
        self.assertTrue(conn.brand_gate_applied)

    def test_brand_filter_can_be_forced_off_per_competitor(self):
        from app.services.price_intelligence.connectors import CrawlSettings
        pages = ([f"https://s.com/products/cervelo-{i}" for i in range(30)]
                 + [f"https://s.com/products/other-{i}" for i in range(30)])
        conn, out = self._conn(pages, settings=CrawlSettings({"brand_filter": "off"}))
        self.assertEqual(60, len(out))
        self.assertFalse(conn.brand_gate_applied)


class DomainConfinementTests(unittest.TestCase):
    """Sitemaps name whatever hosts they like; a page fetched off-site would
    still have its price recorded against this competitor."""

    def test_same_site_accepts_subdomains_and_rejects_lookalikes(self):
        from app.services.price_intelligence.connectors import _same_site
        for url in ("https://example.com/p/1", "https://www.example.com/p/1",
                    "https://shop.example.com/p/1"):
            self.assertTrue(_same_site(url, "https://example.com"), url)
        for url in ("https://evil-example.com/p/1", "https://example.com.evil.net/p",
                    "https://exampleXcom/p/1", ""):
            self.assertFalse(_same_site(url, "https://example.com"), url)

    def test_bare_host_is_accepted_as_the_base(self):
        from app.services.price_intelligence.connectors import _same_site
        self.assertTrue(_same_site("https://shop.example.com/p", "example.com"))

    def test_off_domain_sitemap_entries_are_dropped_and_counted(self):
        from app.services.price_intelligence import connectors
        conn = connectors.GenericSitemapConnector("https://s.com")
        pages = [
            "https://s.com/products/a",
            "https://cdn.othersite.com/products/b",
            "https://s.com.evil.net/products/c",
            "https://shop.s.com/products/d",
        ]
        out = conn._collect_candidates(iter(pages))
        self.assertEqual(["https://s.com/products/a",
                          "https://shop.s.com/products/d"], out)
        self.assertEqual(2, conn.off_domain_dropped)

    def test_confinement_can_be_disabled_per_competitor(self):
        from app.services.price_intelligence import connectors
        conn = connectors.GenericSitemapConnector(
            "https://s.com",
            settings=connectors.CrawlSettings({"confine_to_domain": False}))
        out = conn._collect_candidates(iter(["https://other.com/products/a"]))
        self.assertEqual(["https://other.com/products/a"], out)


class CrawlSettingsTests(unittest.TestCase):
    def test_allow_and_deny_patterns_shape_the_candidate_list(self):
        from app.services.price_intelligence import connectors
        settings = connectors.CrawlSettings({"url_allow_pattern": r"/product/",
                                             "url_deny_pattern": r"/gift-card"})
        conn = connectors.GenericSitemapConnector("https://s.com", settings=settings)
        out = conn._collect_candidates(iter([
            "https://s.com/product/road-frame",
            "https://s.com/about-us",
            "https://s.com/product/gift-card-50",
        ]))
        self.assertEqual(["https://s.com/product/road-frame"], out)

    def test_invalid_regex_is_ignored_not_fatal(self):
        from app.services.price_intelligence.connectors import CrawlSettings
        settings = CrawlSettings({"url_allow_pattern": "([unclosed"})
        self.assertIsNone(settings.allow_pattern)
        self.assertTrue(settings.path_allowed("/anything"))

    def test_unparseable_settings_json_falls_back_to_defaults(self):
        from app.services.price_intelligence.connectors import CrawlSettings
        settings = CrawlSettings("{not json")
        self.assertEqual("auto", settings.brand_filter)
        self.assertIsNone(settings.request_interval)

    def test_page_budget_override(self):
        from app.services.price_intelligence import connectors
        settings = connectors.CrawlSettings({"max_product_pages": 2})
        conn = connectors.GenericSitemapConnector("https://s.com", settings=settings)
        pages = [f"https://s.com/products/x-{i}" for i in range(5)]
        fetched = []
        with patch.object(conn, "_candidate_page_urls", return_value=pages), \
             patch.object(connectors, "polite_get", side_effect=_ok_page(fetched)), \
             patch.object(connectors.config, "MAX_HTML_PRODUCT_PAGES", 300):
            list(conn.iter_products())
        self.assertEqual(2, len(fetched))
        self.assertTrue(conn.cap_hit)

    def test_settings_fall_back_to_live_globals(self):
        from app.services.price_intelligence import connectors
        settings = connectors.CrawlSettings({})
        with patch.object(connectors.config, "MAX_HTML_PRODUCT_PAGES", 7):
            self.assertEqual(7, settings.max_product_pages)


class ThrottleTests(unittest.TestCase):
    def test_per_competitor_interval_reaches_the_throttle(self):
        from app.services.price_intelligence import connectors
        waits = []

        class R:
            status_code = 200
            text = "<html></html>"

        with patch.object(connectors, "_is_public_http_url", return_value=True), \
             patch.object(connectors._robots, "can_fetch", return_value=True), \
             patch.object(connectors._throttle, "wait",
                          side_effect=lambda d, i=None: waits.append(i)), \
             patch.object(connectors._session, "get", return_value=R()):
            connectors.polite_get("https://s.com/x", interval=0.4)
        self.assertEqual([0.4], waits)

    def test_interval_is_clamped(self):
        from app.services.price_intelligence.connectors import _DomainThrottle
        throttle = _DomainThrottle()
        self.assertEqual(_DomainThrottle.MIN_INTERVAL, throttle._base_interval(0.001))
        self.assertEqual(_DomainThrottle.MAX_INTERVAL, throttle._base_interval(9999))
        self.assertEqual(0.5, throttle._base_interval(0.5))
        self.assertEqual(1.0, throttle._base_interval("nonsense"))

    def test_429_backs_the_host_off_for_the_rest_of_the_run(self):
        from app.services.price_intelligence.connectors import _DomainThrottle
        throttle = _DomainThrottle()
        self.assertEqual(1.0, throttle.penalty("s.com"))
        throttle.penalize("s.com")
        throttle.penalize("s.com")
        self.assertEqual(4.0, throttle.penalty("s.com"))
        self.assertEqual(2.0, throttle._base_interval(0.5) * 4 / 1.0)  # 0.5 -> 2.0s
        for _ in range(10):
            throttle.penalize("s.com")
        self.assertEqual(_DomainThrottle.MAX_PENALTY, throttle.penalty("s.com"))
        self.assertEqual(1.0, throttle.penalty("other.com"))  # per host


class CrawlDiagnosticsTests(unittest.TestCase):
    """A zero-product crawl used to be unexplainable. Now it reports which
    filter emptied it, or that the site simply refused us."""

    def test_http_outcomes_are_recorded(self):
        from app.services.price_intelligence import connectors
        conn = connectors.GenericSitemapConnector("https://s.com")
        pages = [f"https://s.com/products/x-{i}" for i in range(3)]

        def fake_get(url, stats=None, **kw):
            if stats is not None:
                stats.record_status(403)
            class R:
                status_code = 403
                text = ""
            return R()

        with patch.object(conn, "_candidate_page_urls", return_value=pages), \
             patch.object(connectors, "polite_get", side_effect=fake_get):
            list(conn.iter_products())
        diag = conn.diagnostics()
        self.assertEqual(0, conn.pages_done)
        self.assertEqual(3, diag["blocked_fetches"])
        self.assertEqual({"403": 3}, diag["status_counts"])

    def test_diagnostics_report_the_filter_funnel(self):
        from app.services.price_intelligence import connectors
        targets = connectors.CrawlTargets(brand_names=["Cervelo"])
        conn = connectors.GenericSitemapConnector("https://s.com", brand_tokens=targets)
        conn._collect_candidates(iter([
            "https://s.com/products/cervelo-a",
            "https://s.com/blogs/news",
            "https://other.com/products/cervelo-b",
        ]))
        diag = conn.diagnostics()
        self.assertEqual(3, diag["sitemap_urls_seen"])
        self.assertEqual(1, diag["candidates_shape_ok"])
        self.assertEqual(1, diag["off_domain_dropped"])

    def test_blocked_crawl_buckets_apart_from_a_bad_sitemap(self):
        from app.services.price_intelligence import repository
        self.assertEqual("blocked", repository._scrape_status_bucket("success_blocked"))
        self.assertEqual("empty", repository._scrape_status_bucket("success_no_products"))

    def test_capped_empty_crawl_is_not_reported_healthy(self):
        """The bucket match was exact, so the " (cap hit — rotating)" suffix the
        runner appends made an empty crawl fall through to "ok"."""
        from app.services.price_intelligence import repository
        self.assertEqual(
            "empty",
            repository._scrape_status_bucket("success_no_products (cap hit — rotating)"))
        self.assertEqual(
            "no_matches",
            repository._scrape_status_bucket("success_no_matches (cap hit — rotating)"))
