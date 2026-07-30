"""Google Merchant Center benchmark ingest.

Three things have to hold for the benchmark to be safe to ship:

  1. Offer ids resolve to the right Lightspeed item. Google identifies products by
     the offer id our Shopify feed submits, which carries no Lightspeed id — the
     bridge is Shopify variant/sku. A silent mis-resolution would attach a market
     price to the wrong product.
  2. Prices convert and get currency-gated. The Merchant API reports micros; a
     factor-of-a-million error, or a USD benchmark leaking into a CAD comparison,
     would read as a wildly mispriced item.
  3. Emitted rows carry the fields the rest of the system filters on — the
     synthetic `source` (so market math excludes them), in_stock/price_scope (so
     they reach the breakdown at all), and every column the loader schema expects.

Missing configuration must skip the phase, never raise: a scrape run has to
survive an unset credential or an uninstalled client library.
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

from app.services.price_intelligence import google_benchmark, repository


class _Price:
    """Stands in for the Merchant API's nested Price message."""

    def __init__(self, amount_micros=None, currency_code=None):
        self.amount_micros = amount_micros
        self.currency_code = currency_code


class _View:
    """A price_competitiveness_product_view / price_insights_product_view row."""

    def __init__(self, offer_id="", id="", title=None, benchmark_price=None,
                 suggested_price=None):
        self.offer_id = offer_id
        self.id = id
        self.title = title
        self.benchmark_price = benchmark_price
        self.suggested_price = suggested_price


# One Shopify variant per Lightspeed item, as get_google_offer_map returns them.
OFFER_MAP = [
    {"variant_id": "31509305786431", "sku": "210000023040",
     "barcode_norm": "889446213", "item_gtin": "889446213", "item_id": "21786"},
    {"variant_id": "40123456789012", "sku": "210000099999",
     "barcode_norm": None, "item_gtin": None, "item_id": "30001"},
]


class OfferResolutionTests(unittest.TestCase):
    def setUp(self):
        self.resolver = google_benchmark.OfferResolver(OFFER_MAP)

    def test_shopify_app_offer_id_resolves_on_trailing_variant_id(self):
        """`shopify_{country}_{productId}_{variantId}` — the default feed format."""
        item_id, how = self.resolver.resolve("shopify_CA_7654321_31509305786431")
        self.assertEqual(item_id, "21786")
        self.assertEqual(how, "variant_id")

    def test_bare_variant_id_resolves(self):
        item_id, how = self.resolver.resolve("31509305786431")
        self.assertEqual((item_id, how), ("21786", "variant_id"))

    def test_offer_id_that_is_the_sku_resolves(self):
        item_id, how = self.resolver.resolve("210000023040")
        self.assertEqual((item_id, how), ("21786", "sku"))

    def test_sku_match_is_case_insensitive(self):
        resolver = google_benchmark.OfferResolver(
            [{"variant_id": "1", "sku": "ABC-123", "item_id": "77"}])
        self.assertEqual(resolver.resolve("abc-123")[0], "77")

    def test_falls_back_to_report_id_last_segment(self):
        """Merchant API joins the composite id with tildes."""
        item_id, how = self.resolver.resolve(
            "unrecognised-offer", "online~en~CA~210000099999")
        self.assertEqual((item_id, how), ("30001", "sku"))

    def test_report_id_also_splits_on_colons(self):
        """The BigQuery transfer of the same report uses colons instead."""
        item_id, _ = self.resolver.resolve("unrecognised-offer", "online:en:CA:210000099999")
        self.assertEqual(item_id, "30001")

    def test_unknown_offer_is_unresolved_not_guessed(self):
        self.assertEqual(self.resolver.resolve("no-such-offer", ""), (None, None))

    def test_rows_without_an_item_id_are_skipped(self):
        resolver = google_benchmark.OfferResolver(
            [{"variant_id": "9", "sku": "s9", "item_id": None}])
        self.assertEqual(resolver.resolve("9"), (None, None))


class PriceParsingTests(unittest.TestCase):
    def test_micros_convert_to_dollars(self):
        amount, currency = google_benchmark._price_parts(_Price(129_990_000, "CAD"))
        self.assertEqual(amount, 129.99)
        self.assertEqual(currency, "CAD")

    def test_accepts_the_flat_dict_shape_too(self):
        """Guards against a client-library shape difference, not a live path."""
        amount, currency = google_benchmark._price_parts(
            {"amount_micros": 5_500_000, "currency_code": "cad"})
        self.assertEqual((amount, currency), (5.5, "CAD"))

    def test_missing_price_yields_none(self):
        self.assertEqual(google_benchmark._price_parts(None), (None, None))
        self.assertEqual(google_benchmark._price_parts(_Price()), (None, None))


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.resolver = google_benchmark.OfferResolver(OFFER_MAP)

    def _collect(self, views, **kw):
        return google_benchmark._collect(
            views, resolver=self.resolver, run_id="run-1",
            observed_at="2026-07-30T00:00:00+00:00",
            competitor_id=google_benchmark.BENCHMARK_COMPETITOR_ID,
            source="gmb_benchmark", price_attr="benchmark_price", **kw)

    def test_emits_a_row_with_every_field_the_loader_expects(self):
        rows, stats = self._collect([
            _View(offer_id="shopify_CA_1_31509305786431", title="Some Tire",
                  benchmark_price=_Price(89_990_000, "CAD")),
        ], country="CA")
        self.assertEqual(stats["written"], 1)
        row = rows[0]
        self.assertEqual(row["source"], "gmb_benchmark")
        self.assertEqual(row["match_item_id"], "21786")
        self.assertEqual(row["price"], 89.99)
        self.assertEqual(row["currency"], "CAD")
        self.assertEqual(row["match_method"], "google_benchmark")
        self.assertEqual(row["diff_key"], "gmb_benchmark:21786:CA")
        # Reaches the per-store breakdown and the chart only if both of these hold.
        self.assertIs(row["in_stock"], True)
        self.assertEqual(row["price_scope"], "variant")
        # Google reports OUR price alongside the benchmark; putting it in
        # compare_at_price would render as a struck-through was-price.
        self.assertIsNone(row["compare_at_price"])
        self.assertIsNone(row["url"])
        self.assertEqual(json.loads(row["variant_options_json"]), [])

    def test_row_columns_match_the_observations_table_schema(self):
        """A stray/missing key fails the load job at 2am, not here — so check now."""
        rows, _ = self._collect([
            _View(offer_id="31509305786431", benchmark_price=_Price(1_000_000, "CAD")),
        ], country="CA")
        expected = {
            "observed_at", "run_id", "source", "diff_key", "competitor_id", "url",
            "competitor_title", "competitor_sku", "gtin", "match_item_id",
            "match_method", "match_confidence", "price", "compare_at_price",
            "currency", "in_stock", "extraction_method", "price_scope",
            "variant_id", "variant_options_json", "price_low", "price_high",
        }
        self.assertEqual(set(rows[0].keys()), expected)

    def test_non_cad_rows_are_dropped(self):
        """A USD benchmark would read ~35% cheap against CAD retails."""
        rows, stats = self._collect([
            _View(offer_id="31509305786431", benchmark_price=_Price(89_990_000, "USD")),
        ], country="CA")
        self.assertEqual(rows, [])
        self.assertEqual(stats["wrong_currency"], 1)

    def test_unresolved_offers_are_counted_not_dropped_silently(self):
        rows, stats = self._collect([
            _View(offer_id="mystery-offer", benchmark_price=_Price(10_000_000, "CAD")),
        ], country="CA")
        self.assertEqual(rows, [])
        self.assertEqual(stats["unresolved"], 1)
        self.assertEqual(stats["returned"], 1)

    def test_priceless_rows_are_counted_separately(self):
        _rows, stats = self._collect([
            _View(offer_id="31509305786431", benchmark_price=_Price(0, "CAD")),
            _View(offer_id="31509305786431", benchmark_price=None),
        ], country="CA")
        self.assertEqual(stats["no_price"], 2)

    def test_suggested_price_uses_its_own_source_and_diff_key(self):
        rows, _ = google_benchmark._collect(
            [_View(offer_id="31509305786431", suggested_price=_Price(74_500_000, "CAD"))],
            resolver=self.resolver, run_id="run-1",
            observed_at="2026-07-30T00:00:00+00:00",
            competitor_id=google_benchmark.SUGGESTED_COMPETITOR_ID,
            source="gmb_suggested", price_attr="suggested_price")
        self.assertEqual(rows[0]["source"], "gmb_suggested")
        self.assertEqual(rows[0]["match_method"], "google_suggested")
        # No country dimension on price_insights_product_view.
        self.assertEqual(rows[0]["diff_key"], "gmb_suggested:21786")
        self.assertEqual(rows[0]["price"], 74.5)


class CredentialTests(unittest.TestCase):
    """An unset or broken credential must skip the phase, never fail the run."""

    def test_unset_credentials_raise_benchmark_unavailable(self):
        with patch.object(google_benchmark.config, "GOOGLE_MERCHANT_CREDENTIALS", "  "):
            with self.assertRaises(google_benchmark.BenchmarkUnavailable):
                google_benchmark._merchant_credentials()

    def test_malformed_inline_json_raises_benchmark_unavailable(self):
        with patch.object(google_benchmark.config, "GOOGLE_MERCHANT_CREDENTIALS",
                          '{"not": "a key"'):
            with self.assertRaises(google_benchmark.BenchmarkUnavailable):
                google_benchmark._merchant_credentials()

    def test_missing_key_file_raises_benchmark_unavailable(self):
        with patch.object(google_benchmark.config, "GOOGLE_MERCHANT_CREDENTIALS",
                          "/nonexistent/merchant-sa.json"):
            with self.assertRaises(google_benchmark.BenchmarkUnavailable):
                google_benchmark._merchant_credentials()

    def test_inline_json_is_read_as_service_account_info(self):
        payload = {"type": "service_account", "client_email": "sa@example.com"}
        with patch.object(google_benchmark.config, "GOOGLE_MERCHANT_CREDENTIALS",
                          json.dumps(payload)):
            with patch("google.oauth2.service_account.Credentials"
                       ".from_service_account_info") as from_info:
                google_benchmark._merchant_credentials()
        from_info.assert_called_once_with(payload)
        from_info.return_value.with_scopes.assert_called_once_with(
            [google_benchmark.CONTENT_SCOPE])

    def test_path_form_is_read_from_file(self):
        with patch.object(google_benchmark.config, "GOOGLE_MERCHANT_CREDENTIALS",
                          "/secrets/merchant-sa.json"):
            with patch("google.oauth2.service_account.Credentials"
                       ".from_service_account_file") as from_file:
                google_benchmark._merchant_credentials()
        from_file.assert_called_once_with("/secrets/merchant-sa.json")

    def test_missing_merchant_id_skips_rather_than_fails(self):
        with patch.object(google_benchmark.config, "GOOGLE_MERCHANT_ID", ""):
            with self.assertRaises(google_benchmark.BenchmarkUnavailable):
                google_benchmark._report_client()


class SyntheticSourceContractTests(unittest.TestCase):
    """The emitted `source` values are what keeps these rows out of market math."""

    def test_emitted_sources_are_the_ones_the_sql_excludes(self):
        self.assertEqual(set(repository.SYNTHETIC_SOURCES),
                         {"gmb_benchmark", "gmb_suggested"})

    def test_sql_filter_names_every_synthetic_source(self):
        clause = repository.sql_market_sources("o")
        for source in repository.SYNTHETIC_SOURCES:
            self.assertIn(f"'{source}'", clause)
        self.assertIn("o.source", clause)

    def test_pseudo_competitors_are_registered_as_never_crawled(self):
        for spec in google_benchmark._COMPETITORS.values():
            self.assertTrue(spec["name"])
        self.assertEqual(
            set(google_benchmark._COMPETITORS),
            {google_benchmark.BENCHMARK_COMPETITOR_ID,
             google_benchmark.SUGGESTED_COMPETITOR_ID})

    def test_ensure_competitors_does_not_reenable_a_disabled_source(self):
        """Re-upserting every run would undo someone turning the source off."""
        existing = [{"competitor_id": google_benchmark.BENCHMARK_COMPETITOR_ID,
                     "enabled": False},
                    {"competitor_id": google_benchmark.SUGGESTED_COMPETITOR_ID,
                     "enabled": True}]
        with patch.object(google_benchmark.repository, "get_competitors",
                          return_value=existing):
            with patch.object(google_benchmark.repository, "upsert_competitor") as upsert:
                google_benchmark.ensure_competitors()
        upsert.assert_not_called()

    def test_ensure_competitors_inserts_missing_sources(self):
        with patch.object(google_benchmark.repository, "get_competitors",
                          return_value=[]):
            with patch.object(google_benchmark.repository, "upsert_competitor") as upsert:
                google_benchmark.ensure_competitors()
        self.assertEqual(upsert.call_count, 2)
        for call in upsert.call_args_list:
            self.assertEqual(call.args[0]["connector_type"],
                             repository.BENCHMARK_CONNECTOR)


if __name__ == "__main__":
    unittest.main()
