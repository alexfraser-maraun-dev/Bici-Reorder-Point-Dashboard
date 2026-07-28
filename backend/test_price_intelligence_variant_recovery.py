"""Variant recovery for JSON-LD storefronts, and what it unblocks downstream.

Three of our sitemap_html competitors (Oak Bay Bikes, The Bike Zone, Enroute)
publish one JSON-LD Offer per purchasable variant but never use schema.org
color/size, so `variant_options` came back empty for all of them: 0 of 26,502
observations over five days carried options, against 3,995 of 3,995 for the one
Magento store. Everything the matcher does with colour and size is gated on that
field, so for those stores the attribute pass never ran — no variant routing and,
worse, no suppression of a clear colour/size conflict. What reached the review
queue was a model-grain fuzzy guess anchored on the highest-revenue variant of
the matrix, which is why an Enroute "Deep Navy / XL" bib was proposed against our
"Dark Choc / M".

The variant label is present on both platforms, just not where schema.org says:
Shopify themes put it in the Offer's sku slot ("Deep Navy / XL"), SmartEtailing
keeps it in the page's spec table keyed by UPC. Fixtures here are trimmed from
the real pages in the report.
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

from app.services.price_intelligence import config, connectors, match_verifier, matcher
from app.services.price_intelligence.scrape_runner import OFF_PAGE_RANK_PENALTY

FIXTURES = os.path.join(os.path.dirname(__file__), "tests", "fixtures")
OAKBAY_URL = "https://www.oakbaybikes.com/product/giro-eclipse-pro-1251894-1.htm"
ENROUTE_URL = "https://enroute.cc/products/mens-maap-team-bib-evo-cargo"


def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


class SmartEtailingSpecTableTests(unittest.TestCase):
    """Oak Bay Bikes / The Bike Zone: Offers carry a gtin and nothing else."""

    @classmethod
    def setUpClass(cls):
        cls.rows = connectors.extract_listings(
            _fixture("oakbay_giro_eclipse_pro.html"), OAKBAY_URL)

    def test_every_variant_is_named_from_the_spec_table(self):
        self.assertEqual(16, len(self.rows))
        self.assertTrue(all(r["variant_options"] for r in self.rows),
                        "every offer should now carry a colour and a size")
        by_gtin = {r["gtin"]: r["variant_options"] for r in self.rows}
        self.assertEqual(["Matte Black", "Medium"], by_gtin["199270032153"])
        self.assertEqual(["Matte White", "Small"], by_gtin["199270032023"])

    def test_a_colourway_containing_a_slash_is_not_split(self):
        """'Matte Black/Frequency Orange / Medium' is two options, not three —
        the dimension separator is a SPACED slash."""
        by_gtin = {r["gtin"]: r["variant_options"] for r in self.rows}
        self.assertEqual(["Matte Black/Frequency Orange", "Medium"],
                         by_gtin["199270032245"])

    def test_offer_level_gtins_stay_distinct(self):
        self.assertEqual(16, len({r["gtin"] for r in self.rows}))

    def test_the_variant_we_stock_is_genuinely_absent_from_this_page(self):
        """Our Matte White / M is UPC 199270032030. Oak Bay lists Matte White in
        Small and Large only, so there is no listing to match — the honest
        outcome for that queue row is 'no match at this store'."""
        gtins = {r["gtin"] for r in self.rows}
        self.assertNotIn("199270032030", gtins)
        whites = sorted(r["variant_options"][1] for r in self.rows
                        if r["variant_options"][0] == "Matte White")
        self.assertEqual(["Large", "Small"], whites)


class ShopifyOfferSkuLabelTests(unittest.TestCase):
    """Enroute: one Product node, one product-level gtin, 30 Offers whose sku
    slot holds the variant label."""

    @classmethod
    def setUpClass(cls):
        cls.rows = connectors.extract_listings(
            _fixture("enroute_maap_bib.html"), ENROUTE_URL)

    def test_options_recovered_from_the_sku_slot(self):
        self.assertEqual(30, len(self.rows))
        self.assertTrue(all(r["variant_options"] for r in self.rows))
        self.assertIn(["Deep Navy", "XL"], [r["variant_options"] for r in self.rows])
        self.assertIn(["Dark Choc", "M"], [r["variant_options"] for r in self.rows])

    def test_parent_gtin_is_not_stamped_onto_every_variant(self):
        """The product-level barcode identifies the product, not any one size.
        Inheriting it collapsed 30 variants onto one match_key and one price
        diff_key — two prices ($225 and $420) alternating as phantom moves."""
        self.assertEqual({None}, {r["gtin"] for r in self.rows})

    def test_variants_keep_distinct_identities_for_diffing(self):
        """With the shared gtin gone, diff_key falls through to the sku, which
        is per-variant — so each colour/size gets its own price series."""
        self.assertEqual(30, len({r["sku"] for r in self.rows}))
        self.assertEqual(30, len({matcher.build_match_key("c1", r) for r in self.rows}))

    def test_prices_differ_across_colourways(self):
        """Guards the reason the collapse mattered: these really are different
        prices, not a stable value repeated."""
        self.assertEqual({225.0, 420.0}, {r["price"] for r in self.rows})


class GtinInheritanceBoundaryTests(unittest.TestCase):
    SINGLE = """
    <html><body><script type="application/ld+json">
    {"@type": "Product", "name": "Giro Eclipse Spherical Helmet",
     "gtin13": "196178193641",
     "offers": {"@type": "Offer", "price": "399.99", "priceCurrency": "CAD",
                "availability": "https://schema.org/InStock"}}
    </script></body></html>"""

    def test_sole_listing_still_inherits_the_product_gtin(self):
        """The fix must not cost us barcodes on ordinary single-variant pages —
        there the product-level gtin IS this listing's identity."""
        rows = connectors.extract_listings(self.SINGLE, "https://s.test/p")
        self.assertEqual(1, len(rows))
        self.assertEqual("196178193641", rows[0]["gtin"])


class OptionLabelParsingTests(unittest.TestCase):
    def test_real_skus_are_not_read_as_option_labels(self):
        """A SKU misread as an option hands the matcher an imaginary colour or
        size to conflict on, which would suppress genuine matches."""
        for sku in ("GR-7202216", "0058217001", "SL-R Black 30-622",
                    "ECL19260533M", "", None):
            self.assertEqual([], connectors._options_from_label(sku), sku)

    def test_labels_are_split_on_the_spaced_slash(self):
        self.assertEqual(["Deep Navy", "XL"],
                         connectors._options_from_label("Deep Navy / XL"))
        self.assertEqual(["Matte Black/Gloss Black", "Small"],
                         connectors._options_from_label("Matte Black/Gloss Black / Small"))


def _item(item_id, title, colour, size, matrix_id="m1", brand="Giro"):
    return {
        "item_id": item_id, "title": title, "brand": brand, "sku": f"sku-{item_id}",
        "upc_normalized": None, "item_matrix_id": matrix_id,
        "matrix_description": title, "attribute_1": colour, "attribute_2": size,
        "attribute_3": None, "current_retail": 479.99,
    }


class ColourFinishWordTests(unittest.TestCase):
    """Nearly every helmet colourway is "Matte something", so the shared finish
    word made unrelated colours look alike — the token that let a wrong-colour
    Oak Bay listing through once its options became visible."""

    def test_finish_word_alone_is_not_a_colour_match(self):
        self.assertFalse(matcher._color_match("Matte Ano Blue", "Matte White"))
        self.assertFalse(matcher._color_match("Gloss Black", "Gloss Red"))

    def test_real_colour_overlap_still_matches(self):
        self.assertTrue(matcher._color_match("Matte White/Silver", "Matte White"))
        self.assertTrue(matcher._color_match("Matte Black/Frequency Orange",
                                             "Black/Frequency Orange"))

    def test_a_colour_that_is_only_a_finish_word_keeps_its_token(self):
        self.assertTrue(matcher._color_match("Matte", "Matte Black"))


class SuppressionOfWrongVariantTests(unittest.TestCase):
    """The queue rows from the report, replayed with options present."""

    GIRO = [
        _item("1", "Giro Eclipse Pro Spherical Helmet", "Matte White", "M"),
        _item("2", "Giro Eclipse Pro Spherical Helmet", "Matte Black", "M"),
        _item("3", "Giro Eclipse Pro Spherical Helmet", "Black/Frequency Orange", "M"),
    ]
    MAAP = [_item("9", "MAAP Men's Team Bib Evo Cargo", "Dark Choc", "M",
                  matrix_id="m2", brand="MAAP")]

    def _candidate(self, tracked, scraped):
        index = matcher.MatchIndex(tracked)
        with patch.object(matcher.settings, "get", return_value=False):
            return index.match(scraped, "k1", competitor_id="c1")[3]

    def test_wrong_size_on_a_matching_colour_is_suppressed(self):
        """Oak Bay's non-Pro Eclipse page: Matte White exists, but in Small."""
        self.assertIsNone(self._candidate(self.GIRO, {
            "title": "Eclipse Spherical Helmet", "brand": "Giro", "sku": None,
            "gtin": "768686477676", "variant_options": ["Matte White/Silver", "Small"],
            "url": "https://www.oakbaybikes.com/product/giro-eclipse-spherical-helmet-414750-1.htm",
        }))

    def test_wrong_colour_on_a_matching_size_is_suppressed(self):
        self.assertIsNone(self._candidate(self.GIRO, {
            "title": "Eclipse Spherical Helmet", "brand": "Giro", "sku": None,
            "gtin": "768686477591", "variant_options": ["Matte Ano Blue", "Medium"],
            "url": "https://www.oakbaybikes.com/product/giro-eclipse-spherical-helmet-414750-1.htm",
        }))

    def test_enroute_bib_in_a_colour_and_size_we_do_not_stock_is_suppressed(self):
        self.assertIsNone(self._candidate(self.MAAP, {
            "title": "Team Bib Evo Cargo", "brand": "MAAP", "sku": "Deep Navy / XL",
            "gtin": None, "variant_options": ["Deep Navy", "XL"], "url": ENROUTE_URL,
        }))

    def test_the_variant_we_actually_stock_is_still_proposed(self):
        """Suppression must not cost us the real match on the same page."""
        candidate = self._candidate(self.MAAP, {
            "title": "Team Bib Evo Cargo", "brand": "MAAP", "sku": "Dark Choc / M",
            "gtin": None, "variant_options": ["Dark Choc", "M"], "url": ENROUTE_URL,
        })
        self.assertIsNotNone(candidate)
        self.assertEqual("9", candidate["item_id"])
        self.assertEqual("variant", candidate["level"])


class MatrixPageAffinityTests(unittest.TestCase):
    """A competitor's product page holds a whole model, exactly as our matrix
    does. Once one is confirmed for any sibling, a proposal at that store on a
    DIFFERENT page is probably a neighbouring model — evidence, not a rule, so
    it is demoted rather than dropped."""

    TRACKED = [
        _item("1", "Giro Eclipse Pro Spherical Helmet", "Matte White", "M"),
        _item("2", "Giro Eclipse Pro Spherical Helmet", "Matte Black", "M"),
    ]
    CONFIRMED = [{
        "status": "confirmed", "match_key": "obb:pro", "item_id": "2",
        "competitor_id": "obb", "confidence": 1.0,
        "competitor_url": "https://www.oakbaybikes.com/product/giro-eclipse-pro-1251894-1.htm",
    }]

    def _candidate(self, url, options=None, competitor_id="obb"):
        index = matcher.MatchIndex(self.TRACKED, links=self.CONFIRMED)
        with patch.object(matcher.settings, "get", return_value=False):
            return index.match({
                "title": "Eclipse Spherical Helmet", "brand": "Giro", "sku": None,
                "gtin": None, "variant_options": options or [], "url": url,
            }, "k1", competitor_id=competitor_id)[3]

    def test_sibling_confirmation_marks_other_pages_off_page(self):
        candidate = self._candidate(
            "https://www.oakbaybikes.com/product/giro-eclipse-spherical-helmet-414750-1.htm")
        self.assertIsNotNone(candidate, "demoted, never dropped")
        self.assertTrue(candidate["off_page"])

    def test_the_confirmed_page_itself_is_not_off_page(self):
        candidate = self._candidate(
            "https://www.oakbaybikes.com/product/giro-eclipse-pro-1251894-1.htm")
        self.assertFalse(candidate["off_page"])

    def test_a_sibling_variant_url_on_the_same_page_is_not_off_page(self):
        """Shopify stores hang every variant off one page via ?variant=, so the
        query string must not make a sibling look like a different page."""
        index = matcher.MatchIndex(self.TRACKED, links=[{
            "status": "confirmed", "match_key": "rs:a", "item_id": "2",
            "competitor_id": "rs", "confidence": 1.0,
            "competitor_url": "https://racersportif.com/products/giro-eclipse-pro?variant=111",
        }])
        with patch.object(matcher.settings, "get", return_value=False):
            candidate = index.match({
                "title": "Giro Eclipse Pro Spherical Helmet", "brand": "Giro",
                "sku": None, "gtin": None, "variant_options": [],
                "url": "https://racersportif.com/products/giro-eclipse-pro?variant=222",
            }, "k2", competitor_id="rs")[3]
        self.assertFalse(candidate["off_page"])

    def test_stores_with_no_confirmed_link_keep_full_freedom(self):
        candidate = self._candidate(
            "https://other.example.com/p/giro-eclipse", competitor_id="other")
        self.assertFalse(candidate["off_page"])

    def test_off_page_attribute_agreement_does_not_earn_exact_confidence(self):
        """Colour+size lining up on a page we know isn't the model's home is a
        coincidence, so it must not take the 0.97 fast path."""
        candidate = self._candidate(
            "https://www.oakbaybikes.com/product/giro-eclipse-spherical-helmet-414750-1.htm",
            options=["Matte White", "Medium"])
        self.assertIsNotNone(candidate)
        self.assertIsNone(candidate["confidence"])

    def test_off_page_candidate_ranks_below_a_clean_one(self):
        """End-to-end of the demotion: mirrors _propose_link's damping and the
        flush sort key that spends the per-item review budget."""
        off = self._candidate(
            "https://www.oakbaybikes.com/product/giro-eclipse-spherical-helmet-414750-1.htm")
        clean = self._candidate(
            "https://www.oakbaybikes.com/product/giro-eclipse-pro-1251894-1.htm")

        def rank(candidate):
            score = candidate.get("confidence") or (candidate["fuzzy_score"] or 0) / 100
            return score * (OFF_PAGE_RANK_PENALTY if candidate["off_page"] else 1)

        self.assertGreater(rank(clean), rank(off))
        self.assertLess(OFF_PAGE_RANK_PENALTY, 1.0)


class PageKeyedLinkTests(unittest.TestCase):
    """A confirmed link keyed on a URL names a page, not a variant.

    Oak Bay's listings carry no SKU, so build_match_key falls back to the URL —
    and all 16 colourways on the Eclipse Pro page share it. Every one of them
    inherited the link confirmed for our Matte Black / M and had its price
    recorded against that item. Invisible while the page's variants were
    indistinguishable; recovering their options is what exposes it.
    """

    TRACKED = [
        _item("1", "Giro Eclipse Pro Spherical Helmet", "Matte Black", "M"),
        {**_item("2", "Giro Eclipse Pro Spherical Helmet", "Black/Frequency Orange", "M"),
         "upc_normalized": "199270032245"},
        _item("3", "Giro Eclipse Pro Spherical Helmet", "Matte White", "M"),
    ]
    PAGE = "https://www.oakbaybikes.com/product/giro-eclipse-pro-1251894-1.htm"
    LINK = [{"status": "confirmed", "match_key": f"obb:{PAGE}", "item_id": "1",
             "competitor_id": "obb", "confidence": 1.0, "competitor_url": PAGE}]

    def _match(self, options, sku=None, gtin=None):
        index = matcher.MatchIndex(self.TRACKED, links=self.LINK)
        with patch.object(matcher.settings, "get", return_value=False):
            return index.match({
                "title": "Eclipse Pro", "brand": "Giro", "sku": sku, "gtin": gtin,
                "variant_options": options, "url": self.PAGE,
            }, f"obb:{self.PAGE}", competitor_id="obb")

    def test_the_linked_variant_still_matches(self):
        item_id, method, _conf, _cand = self._match(["Matte Black", "Medium"])
        self.assertEqual(("1", "link"), (item_id, method))

    def test_a_conflicting_variant_no_longer_inherits_the_link(self):
        item_id, method, _conf, _cand = self._match(["Matte Dark Sage", "Large"])
        self.assertIsNone(item_id, "wrong colour AND size must not report as ours")
        self.assertIsNone(method)

    def test_a_sibling_that_fits_better_wins_the_listing(self):
        """Their 'Matte Black/Frequency Orange' is an exact match for our
        Black/Frequency Orange variant and only a partial one for Matte Black."""
        item_id, _m, _c, candidate = self._match(
            ["Matte Black/Frequency Orange", "Medium"], gtin="199270032245")
        self.assertIsNone(item_id, "must not stay on the Matte Black link")
        self.assertEqual("2", candidate["item_id"])

    def test_listings_without_options_still_trust_the_link(self):
        """Stores we can't read variants for must behave exactly as before."""
        item_id, method, _conf, _cand = self._match([])
        self.assertEqual(("1", "link"), (item_id, method))

    def test_a_sku_keyed_link_is_never_second_guessed(self):
        """A per-variant key IS an identity assertion — often a human's — so the
        guard must not reach it, however odd the colours look."""
        index = matcher.MatchIndex(self.TRACKED, links=[{
            "status": "confirmed", "match_key": "obb:abc123", "item_id": "1",
            "competitor_id": "obb", "confidence": 1.0, "competitor_url": self.PAGE,
        }])
        with patch.object(matcher.settings, "get", return_value=False):
            item_id, method, _conf, _cand = index.match({
                "title": "Eclipse Pro", "brand": "Giro", "sku": "ABC-123",
                "gtin": None, "variant_options": ["Matte Dark Sage", "Large"],
                "url": self.PAGE,
            }, "obb:abc123", competitor_id="obb")
        self.assertEqual(("1", "link"), (item_id, method))


class ModelAnchorAnnotationTests(unittest.TestCase):
    """The Pirelli row: their SL-R is 30-622, we stock only 700c x 28mm. The
    variant shown beside it is the matrix anchor, not a claimed size match, and
    the queue should say so."""

    BY_MATRIX = {"m3": [
        {"item_id": "5", "attribute_1": "Team Edition Yellow", "attribute_2": "700c x 28mm"},
        {"item_id": "6", "attribute_1": "Black", "attribute_2": "700c x 28mm"},
    ]}
    ITEM = {"item_id": "5", "item_matrix_id": "m3",
            "attribute_1": "Team Edition Yellow", "attribute_2": "700c x 28mm"}

    def test_unstocked_size_is_explained_and_left_unresolved(self):
        anchor, resolved, note = match_verifier._resolve_model_anchor(
            self.ITEM, "30-622", self.BY_MATRIX)
        self.assertEqual("5", anchor)
        self.assertFalse(resolved)
        self.assertIn("30-622", note)

    def test_a_size_we_do_stock_still_resolves_without_a_note(self):
        anchor, resolved, note = match_verifier._resolve_model_anchor(
            {**self.ITEM, "item_id": "6"}, "700 x 28c",
            {"m3": [self.BY_MATRIX["m3"][0]]})
        self.assertEqual("5", anchor)
        self.assertTrue(resolved)
        self.assertIsNone(note)


class VerifierContextTests(unittest.TestCase):
    ITEM = _item("1", "Giro Eclipse Pro Spherical Helmet", "Matte White", "M")
    KNOWN = "https://www.oakbaybikes.com/product/giro-eclipse-pro-1251894-1.htm"

    def _pair(self, url, known):
        return match_verifier._build_pair(
            {"link_id": "l1", "competitor_title": "Eclipse Spherical Helmet",
             "competitor_sku": None, "their_price": 399.99, "competitor_url": url,
             "variant_options_json": json.dumps(["Matte Ano Blue", "Medium"])},
            self.ITEM, known)

    def test_known_page_is_sent_when_it_contradicts_the_proposal(self):
        pair = self._pair(
            "https://www.oakbaybikes.com/product/giro-eclipse-spherical-helmet-414750-1.htm",
            self.KNOWN)
        self.assertEqual(self.KNOWN, pair["ours"]["model_already_matched_at_this_store"])

    def test_known_page_is_omitted_when_it_is_this_very_page(self):
        pair = self._pair(self.KNOWN, self.KNOWN)
        self.assertNotIn("model_already_matched_at_this_store", pair["ours"])

    def test_recovered_options_reach_the_model(self):
        pair = self._pair(self.KNOWN, None)
        self.assertEqual(["Matte Ano Blue", "Medium"], pair["theirs"]["variant_options"])


class TrimTierPromptTests(unittest.TestCase):
    def test_prompt_names_the_regression_it_guards(self):
        """'Eclipse' vs 'Eclipse Pro' came back same_model twice — the rule was
        buried in a list, so it is now stated as its own directive with the
        exact failing pair as the example."""
        prompt = match_verifier.SYSTEM_PROMPT
        self.assertIn("TRIM RULE", prompt)
        self.assertIn("Eclipse Pro", prompt)
        self.assertIn("model_already_matched_at_this_store", prompt)


if __name__ == "__main__":
    unittest.main()
