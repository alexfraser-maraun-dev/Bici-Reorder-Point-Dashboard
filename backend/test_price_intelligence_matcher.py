"""Attribute-match confidence gating.

Colour/size agreement is only evidence of "same variant" when the model anchor
the fuzzy pass picked is itself credible. The live regression this guards: our
"Sweet Protection Fluxer Mips Helmet" scored ~66 against their "Sweet Protection
Falconer 2Vi MIPS Helmet", the shared Matte Black / M-L then resolved as an
exact attribute match, and the pair was proposed at confidence 0.97 — which
outranks every genuine candidate in the review queue's priority sort and burned
the per-item (5) and per-run (200) LLM budgets.

Measured on live data before this gate: 384 attr proposals carried confidence
0.97 at an average fuzzy score of 71.5, and 369 of them (96%) were rejected by
the verifier as different products. The attr proposals that scored ~87 had a 0%
rejection rate — hence a default anchor floor of 80.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

from app.services.price_intelligence import config, matcher, scrape_runner


def _item(item_id, title, colour, size, matrix_id="m1", matrix_desc=None, brand="Sweet Protection"):
    return {
        "item_id": item_id, "title": title, "brand": brand, "sku": f"sku-{item_id}",
        "upc_normalized": None, "item_matrix_id": matrix_id,
        "matrix_description": matrix_desc or title,
        "attribute_1": colour, "attribute_2": size, "attribute_3": None,
        "current_retail": 199.99,
    }


class AttrAnchorConfidenceTests(unittest.TestCase):
    # Our catalog: one helmet model, two colour/size variants.
    TRACKED = [
        _item("1", "Sweet Protection Fluxer Mips Helmet", "Matte Black", "Large"),
        _item("2", "Sweet Protection Fluxer Mips Helmet", "Satin White", "Small"),
    ]

    # The fixture's titles are short and share "Sweet Protection … MIPS Helmet",
    # so rapidfuzz scores the wrong-model pair ~87 where production scored ~66.
    # Tests therefore pin an explicit threshold and assert the *mechanism*,
    # rather than depending on the exact ratio of these particular strings.
    WEAK_TITLE = "Sweet Protection Falconer 2Vi MIPS Helmet - Matte Black / Large"
    STRONG_TITLE = "Sweet Protection Fluxer Mips Helmet - Matte Black / Large"

    def _match(self, title, options, auto_confirm=False, min_score=90.0):
        # matcher.match reads the runtime auto-confirm setting from BigQuery;
        # stub it so these stay pure unit tests.
        index = matcher.MatchIndex(self.TRACKED)
        with patch.object(matcher.settings, "get", return_value=auto_confirm), \
             patch.object(config, "ATTR_ANCHOR_MIN_SCORE", min_score):
            return index.match({
                "title": title, "brand": "Sweet Protection", "sku": "x1",
                "gtin": None, "variant_options": options, "url": "https://s.com/p",
            })

    def test_weak_anchor_does_not_earn_exact_match_confidence(self):
        """A different model sharing colour+size must not be proposed at 0.97."""
        _id, _method, _conf, candidate = self._match(
            self.WEAK_TITLE, ["Matte Black", "Large"])
        self.assertIsNotNone(candidate, "pair should still be reviewable")
        self.assertLess(candidate["fuzzy_score"], 90.0)
        self.assertIsNone(
            candidate["confidence"],
            "weak-anchor attr match must fall back to its real fuzzy score")

    def test_strong_anchor_still_earns_exact_match_confidence(self):
        """The real same-model case keeps its high-confidence fast path."""
        _id, _method, _conf, candidate = self._match(
            self.STRONG_TITLE, ["Matte Black", "Large"])
        self.assertIsNotNone(candidate)
        self.assertGreaterEqual(candidate["fuzzy_score"], 90.0)
        self.assertEqual(0.97, candidate["confidence"])
        self.assertEqual("1", candidate["item_id"])  # routed to the Matte Black / Large variant

    def test_default_threshold_covers_the_observed_garbage_band(self):
        """Live rejected attr proposals averaged 65-78 fuzzy; the shipped
        default must sit above that band so they lose the 0.97 fast path."""
        self.assertGreaterEqual(config.ATTR_ANCHOR_MIN_SCORE, 80.0)

    def test_weak_anchor_blocks_attr_auto_confirm(self):
        """With auto-confirm on, a weak anchor must not hard-confirm a link."""
        with patch.object(config, "ATTR_AUTO_CONFIRM", True):
            item_id, method, _conf, _cand = self._match(
                self.WEAK_TITLE, ["Matte Black", "Large"], auto_confirm=True)
        self.assertNotEqual("attr_exact", method)
        self.assertIsNone(item_id)

    def test_strong_anchor_auto_confirms_when_enabled(self):
        with patch.object(config, "ATTR_AUTO_CONFIRM", True):
            item_id, method, conf, _cand = self._match(
                "Sweet Protection Fluxer Mips Helmet - Satin White / Small",
                ["Satin White", "Small"], auto_confirm=True)
        self.assertEqual("attr_exact", method)
        self.assertEqual(0.97, conf)
        self.assertEqual("2", item_id)

    def test_clear_attribute_conflict_is_still_suppressed(self):
        """Suppression is a 'don't propose' call and stays anchor-independent."""
        _id, _m, _c, candidate = self._match(
            "Sweet Protection Fluxer Mips Helmet - Neon Yellow / XXL",
            ["Neon Yellow", "XXL"])
        self.assertIsNone(candidate)

    def test_queue_sort_puts_genuine_candidate_above_weak_attr_pair(self):
        """End-to-end of the actual harm: the flush sort key must rank a real
        fuzzy match above a coincidental colour/size pair."""
        _i, _m, _c, weak_attr = self._match(self.WEAK_TITLE, ["Matte Black", "Large"])
        _i2, _m2, _c2, genuine = self._match(self.STRONG_TITLE, ["Matte Black", "Large"])
        # Mirrors scrape_runner's pending_links.sort key.
        key = lambda r: r.get("confidence") or (r.get("fuzzy_score") or 0) / 100
        self.assertGreater(key(genuine), key(weak_attr))


class CombinedSizeTests(unittest.TestCase):
    """The live regression this file's helmets keep producing.

    Combined letter sizes used to agree on set OVERLAP, so 'M/L' matched 'L/XL'
    (they share L) and 'S/M' (they share M) — every size in a brand's size run
    matched its neighbours. Steed's Tucker III L/XL and S/M listings were both
    proposed against our M/L item at 100% with a 'color+size match' badge, while
    our actual L/XL and S/M sat unmatched in the same matrix.
    """

    def test_two_different_combined_sizes_are_not_the_same_size(self):
        self.assertFalse(matcher._size_value_matches("M/L", "L/XL"))
        self.assertFalse(matcher._size_value_matches("M/L", "S/M"))
        self.assertFalse(matcher._size_value_matches("L/XL (59-61cm)", "M/L"))
        self.assertFalse(matcher._size_value_matches("S/M (53-56cm)", "M/L"))

    def test_a_combined_size_still_agrees_with_either_half(self):
        """Guards the deliberate behaviour in variant_coverage against
        over-correction: their one M/L item against our separate M and L is
        genuinely ambiguous and must stay a review decision."""
        self.assertEqual("partial", matcher._size_agreement("M/L", "Medium"))
        self.assertEqual("partial", matcher._size_agreement("M/L", "Large"))
        self.assertEqual("partial", matcher._size_agreement("Small/Medium", "S"))

    def test_the_same_combined_size_is_an_exact_match(self):
        self.assertEqual("exact", matcher._size_agreement("L/XL (59-61cm)", "L/XL"))
        self.assertEqual("exact", matcher._size_agreement("S/M (53-56cm)", "S/M"))


class ColourGradeTests(unittest.TestCase):
    def test_a_shared_generic_colour_word_is_not_an_exact_match(self):
        """'Satin White' vs 'Bronco White' share only 'white' (satin is a finish
        word). Still an agreement — a false mismatch would tombstone a real
        colourway — but not one that earns exact-match confidence."""
        self.assertEqual("partial", matcher._color_agreement("Satin White", "Bronco White"))
        self.assertEqual("partial", matcher._color_agreement("Matte Black", "Carbon Black"))

    def test_the_same_colourway_spelled_differently_is_exact(self):
        self.assertEqual("exact", matcher._color_agreement("Matte Black", "Gloss Black"))

    def test_unrelated_colours_still_disagree(self):
        self.assertIsNone(matcher._color_agreement("Matte Ano Blue", "Matte White"))


class TuckerIIIRoutingTests(unittest.TestCase):
    """End to end on the three rows that were live in the queue: their listing
    must land on the variant it names, not on whichever sibling happened to be
    first in revenue order."""

    MODEL = "Sweet Protection Tucker III 2Vi Mips Helmet"

    def _tracked(self):
        # Revenue order, which is the order rows arrive in — M/L first, so a
        # positional pick lands on it.
        return [
            _item("357", self.MODEL, "Matte Black", "M/L", matrix_id="t1"),
            _item("356", self.MODEL, "Matte Black", "S/M", matrix_id="t1"),
            _item("358", self.MODEL, "Matte Black", "L/XL", matrix_id="t1"),
            _item("355", self.MODEL, "Satin White", "L/XL", matrix_id="t1"),
        ]

    def _candidate(self, title, options):
        index = matcher.MatchIndex(self._tracked())
        with patch.object(matcher.settings, "get", return_value=False):
            return index.match({
                "title": title, "brand": "Sweet Protection", "sku": None,
                "gtin": None, "variant_options": options,
                "url": "https://steedcycles.com/products/tucker-iii",
            }, match_key="steed:x", competitor_id="steed")[3]

    def test_their_lxl_routes_to_our_lxl_not_our_ml(self):
        candidate = self._candidate(
            f"{self.MODEL} - Matte Black / L/XL (59-61cm)",
            ["Matte Black", "L/XL (59-61cm)"])
        self.assertEqual("358", candidate["item_id"])
        self.assertEqual("attr", candidate["method"])
        self.assertEqual(0.97, candidate["confidence"])

    def test_their_sm_routes_to_our_sm(self):
        candidate = self._candidate(
            f"{self.MODEL} - Matte Black / S/M (53-56cm)",
            ["Matte Black", "S/M (53-56cm)"])
        self.assertEqual("356", candidate["item_id"])
        self.assertEqual("attr", candidate["method"])

    def test_an_unstocked_colourway_is_reviewable_but_not_exact(self):
        """We stock no Bronco White. The listing stays visible — it may be a
        colourway we do carry under another name — but it must not claim the
        0.97 an exact colour+size resolution earns."""
        candidate = self._candidate(
            f"{self.MODEL} - Bronco White / L/XL (59-61cm)",
            ["Bronco White", "L/XL (59-61cm)"])
        self.assertEqual("attr_partial", candidate["method"])
        self.assertEqual(matcher.ATTR_PARTIAL_CONFIDENCE, candidate["confidence"])


class VariantOptionParsingTests(unittest.TestCase):
    def test_variant_options_keep_a_combined_size_intact(self):
        """Splitting on every '/' shattered the values that carry one, which
        re-broke the combined size into halves on the verifier + cleanup paths."""
        self.assertEqual(
            ["Matte Black", "L/XL (59-61cm)"],
            matcher.parse_variant_options(
                "Sweet Protection Tucker III 2Vi MIPS Helmet - Matte Black / L/XL (59-61cm)"))

    def test_a_two_tone_colourway_stays_one_option(self):
        self.assertEqual(
            ["Hydrogen White/Uranium Black Matte", "L (56-61cm)"],
            matcher.parse_variant_options(
                "POC Cytal Helmet - Hydrogen White/Uranium Black Matte / L (56-61cm)"))

    def test_a_plain_two_option_tail_is_unchanged(self):
        self.assertEqual(
            ["Steel Green", "58"],
            matcher.parse_variant_options("Factor Monza Force - Steel Green / 58"))


class RejectedPairSuppressionTests(unittest.TestCase):
    """Rejecting a listing tombstoned its match_key only, so the sibling listing
    for the same item at the same store returned on the next run."""

    def _proposer(self, rejected_pairs):
        return scrape_runner.LinkProposer(
            {"1": {"current_retail": 439.99}}, set(), set(), rejected_pairs)

    def test_rejecting_one_listing_blocks_its_siblings_at_that_store(self):
        proposer = self._proposer({("1", "steed")})
        row = proposer.propose(
            "steed:other-listing",
            {"item_id": "1", "method": "fuzzy_title", "confidence": 0.8,
             "fuzzy_score": 100.0, "level": "model"},
            "steed", {"url": "https://steedcycles.com/p/2", "title": "x"})
        self.assertIsNone(row)

    def test_a_barcode_match_still_gets_through_a_rejected_pair(self):
        """Identity is new evidence, not a retry of the guess the human turned
        down — otherwise one rejection shuts the store out of the item forever."""
        proposer = self._proposer({("1", "steed")})
        row = proposer.propose(
            "steed:gtin:7048653265486", None, "steed",
            {"url": "https://steedcycles.com/p/3", "title": "x", "gtin": "7048653265486"},
            item_id="1", method="gtin", confidence=1.0)
        self.assertIsNotNone(row)

    def test_an_unrejected_pair_is_untouched(self):
        proposer = self._proposer({("2", "steed")})
        row = proposer.propose(
            "steed:listing", {"item_id": "1", "method": "fuzzy_title",
                              "confidence": 0.8, "fuzzy_score": 95.0, "level": "model"},
            "steed", {"url": "https://steedcycles.com/p/4", "title": "x"})
        self.assertIsNotNone(row)
        self.assertEqual("llm", row["source"])

    def test_a_partial_attr_proposal_carries_its_own_source(self):
        proposer = self._proposer(set())
        row = proposer.propose(
            "steed:listing2",
            {"item_id": "1", "method": "attr_partial", "confidence": 0.75,
             "fuzzy_score": 100.0, "level": "variant"},
            "steed", {"url": "https://steedcycles.com/p/5", "title": "x"})
        self.assertEqual("attr_partial", row["source"])


class DualKeyAliasTests(unittest.TestCase):
    """One listing, two match_key spellings. The catalog crawl keys a Shopify
    variant on its SKU (products.json has no barcode); the fan-out keys the same
    variant on the barcode from /products/<handle>.js. When the SKU is the
    barcode, the second row was a pending duplicate of a confirmed link."""

    SKU = "0012345678905"

    def test_a_barcode_length_sku_aliases_the_gtin_key(self):
        aliases = matcher.match_key_aliases("c1", {"sku": self.SKU})
        self.assertEqual({"c1:gtin:12345678905"}, aliases)

    def test_a_listing_with_barcode_and_numeric_sku_aliases_the_sku_key(self):
        aliases = matcher.match_key_aliases("c1", {"sku": self.SKU, "gtin": self.SKU})
        self.assertEqual({"c1:gtin:12345678905", f"c1:{self.SKU}"}, aliases)

    def test_a_short_numeric_sku_is_not_a_barcode(self):
        self.assertEqual(set(), matcher.match_key_aliases("c1", {"sku": "12345"}))
        self.assertEqual(set(), matcher.match_key_aliases("c1", {"sku": "POC39285361T"}))

    def _proposer(self, existing):
        return scrape_runner.LinkProposer({"1": {"current_retail": 99.0}}, existing, set())

    def test_proposer_skips_a_listing_already_linked_under_its_barcode_spelling(self):
        proposer = self._proposer({"c1:gtin:12345678905"})
        row = proposer.propose(
            f"c1:{self.SKU}",
            {"item_id": "1", "method": "attr", "confidence": 0.97,
             "fuzzy_score": 93.0, "level": "variant"},
            "c1", {"sku": self.SKU, "url": "https://c1.com/p/1", "title": "x"})
        self.assertIsNone(row)

    def test_proposer_skips_a_listing_already_linked_under_its_sku_spelling(self):
        proposer = self._proposer({f"c1:{self.SKU}"})
        row = proposer.propose(
            "c1:gtin:12345678905", None, "c1",
            {"sku": self.SKU, "gtin": self.SKU, "url": "https://c1.com/p/1", "title": "x"},
            item_id="1", method="gtin", confidence=1.0)
        self.assertIsNone(row)

    def test_an_admitted_proposal_reserves_its_other_spelling_for_the_run(self):
        proposer = self._proposer(set())
        row = proposer.propose(
            f"c1:{self.SKU}",
            {"item_id": "1", "method": "attr", "confidence": 0.97,
             "fuzzy_score": 93.0, "level": "variant"},
            "c1", {"sku": self.SKU, "url": "https://c1.com/p/1", "title": "x"})
        self.assertIsNotNone(row)
        self.assertIn("c1:gtin:12345678905", proposer.existing_link_keys)

    def test_rejected_keys_are_not_widened_by_sku_aliases(self):
        """A SKU-form rejection must never shadow a barcode-form confirmed link:
        match() checks rejections before every tier, so the confirmed link would
        go dark and its price observations would stop."""
        tracked = [_item("1", "Continental GP5000 S TR", "Black", "700c x 25mm", brand="Continental")]
        confirmed = [{"status": "confirmed", "item_id": "1", "competitor_id": "c1",
                      "match_key": "c1:gtin:12345678905", "gtin": self.SKU,
                      "confidence": 1.0, "competitor_url": "https://c1.com/p/1",
                      "variant_options_json": None}]
        index = matcher.MatchIndex(tracked, links=confirmed,
                                   rejected_keys={f"c1:{self.SKU}"})
        item_id, method, confidence, _ = index.match(
            {"title": "GP5000 S TR", "brand": "Continental", "sku": self.SKU,
             "gtin": self.SKU, "url": "https://c1.com/p/1"},
            match_key="c1:gtin:12345678905", competitor_id="c1")
        self.assertEqual(("1", "link", 1.0), (item_id, method, confidence))


class ListingOptionsTests(unittest.TestCase):
    def test_structured_options_win_over_the_title_tail(self):
        row = {"variant_options_json": '["Hydrogen White Matt", "Medium"]',
               "competitor_title": "Ventral MIPS - Other / Large"}
        self.assertEqual(["Hydrogen White Matt", "Medium"], matcher.listing_options(row))

    def test_blank_or_malformed_options_fall_back_to_the_title(self):
        self.assertEqual(["Black", "58"], matcher.listing_options(
            {"variant_options_json": "[]", "competitor_title": "Monza - Black / 58"}))
        self.assertEqual(["Black", "58"], matcher.listing_options(
            {"variant_options_json": "not json", "competitor_title": "Monza - Black / 58"}))
        self.assertEqual(["Black", "58"], matcher.listing_options(
            {"variant_options_json": '["", null]', "competitor_title": "Monza - Black / 58"}))

    def test_a_bare_title_and_no_options_is_empty(self):
        self.assertEqual([], matcher.listing_options(
            {"variant_options_json": None, "competitor_title": "Ventral MIPS"}))


if __name__ == "__main__":
    unittest.main()
