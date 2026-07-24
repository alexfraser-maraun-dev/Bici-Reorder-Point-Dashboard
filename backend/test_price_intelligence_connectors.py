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
