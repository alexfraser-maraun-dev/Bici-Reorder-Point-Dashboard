"""Variant coverage: the size formats and listing identities that were losing it.

Model discovery was never the problem — 75 of 85 tracked models had a confirmed
link at some store. Variants did: 160 of 372. The gap tracked exactly one thing,
the shape of a store's size labels. Where sizes are plain numbers (Cannondale
frames, "54") coverage was 100%; where they are apparel sizes it collapsed:

    Cannondale SuperSix EVO 2 @ Wheels of Bloor : 16 of 16
    POC Omne Air Mips         @ Racer Sportif   :  3 of 36
    POC Cytal                 @ The Bike Zone   :  1 of 27

Two independent causes, both here:

  1. `_canon_size` tested for digits before letter sizes, so "L (56-61cm)" read
     as the numbers 56 and 61 and "2XL" as the number 2. Neither can ever equal
     our "Large" or "XXL", so the size read as a *conflict* and the listing was
     suppressed — the correct variant never reached the review queue at all.
     Measured live: 3,348 mis-canonicalised option values in 24 hours.

  2. `build_match_key` ignored the barcode. SmartEtailing stores publish a UPC on
     every variant and no SKU at all, so all 27 variants of a page produced one
     URL-derived key, and only the first could ever own a link row.

Then the fan-out that the two fixes unlock: a confirmed link means a human said
this store sells this model on this page, and the page enumerates its own
variants, so our remaining variants can be paired off it for free.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from app.services.price_intelligence import matcher
from app.services.price_intelligence.scrape_runner import _sibling_matches


def _variant(item_id, colour, size, matrix_id="cytal", upc=None):
    return {
        "item_id": item_id, "title": "POC Cytal Helmet", "brand": "POC",
        "sku": f"sku-{item_id}", "upc_normalized": upc,
        "item_matrix_id": matrix_id, "matrix_description": "POC Cytal Helmet",
        "attribute_1": colour, "attribute_2": size, "attribute_3": None,
        "current_retail": 450.0,
    }


class LetterSizeCanonTests(unittest.TestCase):
    """Every way the live catalogs spell a size has to reach one canon token."""

    def test_a_size_qualified_by_a_head_circumference_is_a_letter_size(self):
        for label in ("L (56-61cm)", "M (54-59cm)", "S (50-56cm)",
                      "S (52-55.5cm)", "XL (46+)", "M (40-42)"):
            with self.subTest(label=label):
                kind, _ = matcher._canon_size(label)
                self.assertEqual("letter", kind)

    def test_numeric_apparel_sizes_are_not_read_as_numbers(self):
        self.assertTrue(matcher._size_value_matches("2XL", "XXL"))
        self.assertTrue(matcher._size_value_matches("3XL", "XXXL"))

    def test_the_live_poc_pair_matches(self):
        self.assertTrue(matcher._size_value_matches("L (56-61cm)", "Large"))
        self.assertTrue(matcher._size_value_matches("M (54-59cm)", "Medium"))
        self.assertTrue(matcher._size_value_matches("S (50-56cm)", "Small"))

    def test_a_combined_size_agrees_with_either_half(self):
        # A store selling one M/L item against our separate M and L is genuinely
        # ambiguous — both must match, so the routing stays a review decision
        # instead of being silently resolved or suppressed.
        self.assertTrue(matcher._size_value_matches("M/L", "Medium"))
        self.assertTrue(matcher._size_value_matches("M/L", "Large"))
        self.assertTrue(matcher._size_value_matches("Small/Medium", "S"))
        self.assertTrue(matcher._size_value_matches("Large/X-Large", "XL"))

    def test_numeric_sizes_are_untouched(self):
        self.assertTrue(matcher._size_value_matches("54", "54cm"))
        self.assertTrue(matcher._size_value_matches("700 x 28", "700c x 28mm"))
        self.assertFalse(matcher._size_value_matches("54", "56"))

    def test_a_colourway_naming_a_size_word_is_still_a_colour(self):
        # "Medium Clay Pearl" is a real POC colourway. Reading it as a size would
        # invent a size conflict on a listing whose size is fine.
        self.assertIsNone(matcher._canon_size("Medium Clay Pearl"))
        self.assertIsNone(matcher._canon_size("Uranium Black Matt"))

    def test_letter_and_numeric_sizes_never_cross_match(self):
        self.assertFalse(matcher._size_value_matches("Large", "58"))


class SuppressedPocVariantTests(unittest.TestCase):
    """The live regression, end to end: Steed's Cytal listing against ours."""

    OURS = ["Hydrogen White/Uranium Black Matt", "Large"]
    THEIRS = ["Hydrogen White/Uranium Black Matte", "L (56-61cm)"]

    def test_the_pair_no_longer_conflicts(self):
        self.assertFalse(matcher.attributes_conflict(self.THEIRS, self.OURS))

    def test_the_pair_scores_as_an_exact_variant_match(self):
        # It scored -1.0 before: the size counted as a hard conflict, which also
        # made the correct pair lose the verifier's 1:1 tie-break to wrong ones.
        self.assertEqual(2.0, matcher.attribute_match_score(self.THEIRS, self.OURS))

    def test_a_genuinely_wrong_size_still_conflicts(self):
        self.assertTrue(matcher.attributes_conflict(
            ["Hydrogen White/Uranium Black Matte", "S (50-56cm)"], self.OURS))


class ModelGrainTitleTests(unittest.TestCase):
    def test_a_long_hyphenated_option_tail_is_stripped(self):
        self.assertEqual(
            "POC Cytal Helmet",
            matcher.strip_variant_tokens(
                "POC Cytal Helmet - Hydrogen White/Uranium Black Matte / L (56-61cm)"))

    def test_a_plain_colour_tail_still_works(self):
        self.assertEqual(
            "Cannondale Synapse Carbon 5",
            matcher.strip_variant_tokens("Cannondale Synapse Carbon 5 - Phoenix Yellow / 58"))

    def test_a_title_with_no_tail_is_unchanged(self):
        self.assertEqual(
            "Continental Grand Prix 5000 Tire",
            matcher.strip_variant_tokens("Continental Grand Prix 5000 Tire"))


class BrandNormalisedTitleTests(unittest.TestCase):
    """Our titles lead with the brand; most competitors' titles don't."""

    def test_the_brand_is_removed_from_both_sides(self):
        self.assertEqual("cytal helmet", matcher.strip_brand("POC Cytal Helmet", "POC"))
        self.assertEqual(
            "synapse carbon 5",
            matcher.strip_brand("Cannondale Synapse Carbon 5", "Cannondale"))

    def test_a_title_that_is_only_the_brand_keeps_its_text(self):
        self.assertEqual("poc", matcher.strip_brand("POC", "POC"))

    def test_a_brand_scoped_corpus_holds_only_that_brand(self):
        index = matcher.MatchIndex([
            _variant("1", "Uranium Black Matt", "Large"),
            {"item_id": "9", "title": "Cannondale Synapse Carbon 5",
             "brand": "Cannondale", "sku": "c1", "upc_normalized": None,
             "item_matrix_id": "syn", "matrix_description": "Cannondale Synapse Carbon 5",
             "attribute_1": "Black", "attribute_2": "54", "attribute_3": None},
        ])
        poc = index._brandless_corpus(index.titles_by_brand, "poc")
        self.assertEqual(["cytal helmet"], poc["texts"])


class BarcodeIdentityTests(unittest.TestCase):
    """A page's variants must not collapse onto one key."""

    def test_barcode_beats_url_when_the_listing_has_no_sku(self):
        page = "https://www.thebikezone.com/product/poc-cytal-1244515-1.htm"
        first = matcher.build_match_key("bz", {
            "sku": None, "url": page, "gtin": "07325549823846", "title": "POC Cytal"})
        second = matcher.build_match_key("bz", {
            "sku": None, "url": page, "gtin": "07325549823761", "title": "POC Cytal"})
        self.assertNotEqual(first, second)

    def test_the_key_is_stable_across_upc_and_ean_spellings(self):
        self.assertEqual(
            matcher.build_match_key("bz", {"gtin": "07325549823846"}),
            matcher.build_match_key("bz", {"gtin": "7325549823846"}))

    def test_a_barcode_too_short_to_be_one_is_ignored(self):
        key = matcher.build_match_key("bz", {"gtin": "12", "sku": "REAL-SKU"})
        self.assertEqual("bz:realsku", key)

    def test_a_sku_keyed_listing_is_unaffected(self):
        self.assertEqual(
            "bz:realsku",
            matcher.build_match_key("bz", {"gtin": None, "sku": "REAL-SKU"}))

    def test_a_link_stored_under_its_old_key_still_matches(self):
        # The re-key migration is hygiene, not a prerequisite: a link written
        # before the change carries its own gtin, so the barcode form is derivable.
        index = matcher.MatchIndex(
            [_variant("1", "Uranium Black Matt", "Large")],
            links=[{"status": "confirmed", "item_id": "1", "competitor_id": "bz",
                    "match_key": "bz:https://www.thebikezone.com/product/poc-cytal-1.htm",
                    "competitor_url": "https://www.thebikezone.com/product/poc-cytal-1.htm",
                    "gtin": "07325549823846", "confidence": 1.0}])
        self.assertIn("bz:gtin:7325549823846", index.by_link)


class SiblingFanOutTests(unittest.TestCase):
    """Pairing a confirmed page's other variants with our other variants."""

    SIBLINGS = [
        _variant("1", "Uranium Black Matt", "Small", upc="7325549823518"),
        _variant("2", "Uranium Black Matt", "Medium", upc="7325549823501"),
        _variant("3", "Fluorescent Orange Matt", "Large", upc="7325549823624"),
    ]

    def _listing(self, options, gtin=None, price=450.0):
        return {"title": "POC Cytal", "variant_options": options, "gtin": gtin,
                "price": price, "sku": None, "price_scope": "variant"}

    def test_a_barcode_pairs_as_an_identity(self):
        pairs = list(_sibling_matches(
            [self._listing(["Uranium Black Matt", "Medium"], gtin="07325549823501")],
            self.SIBLINGS, set()))
        self.assertEqual([("2", "gtin")], [(i, m) for _l, i, m in pairs])

    def test_colour_and_size_pair_for_review(self):
        pairs = list(_sibling_matches(
            [self._listing(["Fluorescent Orange Matte", "L (56-61cm)"])],
            self.SIBLINGS, set()))
        self.assertEqual([("3", "sibling")], [(i, m) for _l, i, m in pairs])

    def test_a_variant_we_do_not_stock_is_skipped(self):
        self.assertEqual([], list(_sibling_matches(
            [self._listing(["Apatite Navy Matt", "S (50-56cm)"])],
            self.SIBLINGS, set())))

    def test_an_ambiguous_listing_claims_nothing(self):
        # Two of our variants are Uranium Black; a listing naming only the colour
        # could be either, and guessing is how one item collects a whole model.
        self.assertEqual([], list(_sibling_matches(
            [self._listing(["Uranium Black Matt"])], self.SIBLINGS, set())))

    def test_a_variant_already_claimed_is_not_claimed_twice(self):
        self.assertEqual([], list(_sibling_matches(
            [self._listing(["Fluorescent Orange Matte", "Large"])],
            self.SIBLINGS, {"3"})))

    def test_a_price_range_is_not_a_variant_price(self):
        listing = self._listing(["Fluorescent Orange Matte", "Large"])
        listing["price_scope"] = "range"
        self.assertEqual([], list(_sibling_matches([listing], self.SIBLINGS, set())))


class ColourwayPageTests(unittest.TestCase):
    """Stores that give each colourway its own page are the common shape here —
    one lists ASSOS MILLE GT across 37 pages — so a new colourway on a new page
    must not inherit the neighbouring-model demotion."""

    TRACKED = [
        _variant("1", "Uranium Black Matt", "Large"),
        _variant("2", "Fluorescent Orange Matt", "Large"),
    ]
    CONFIRMED = [{
        "status": "confirmed", "item_id": "1", "competitor_id": "rs",
        "confidence": 1.0, "variant_options_json": '["Uranium Black Matte", "Large"]',
        "competitor_url": "https://www.racersportif.com/products/poc-cytal-black",
    }]

    def _index(self):
        return matcher.MatchIndex(self.TRACKED, links=self.CONFIRMED)

    OTHER_PAGE = "https://www.racersportif.com/products/poc-cytal-orange"

    def test_a_new_colourway_on_a_new_page_is_not_off_page(self):
        self.assertFalse(self._index()._off_page(
            "2", "rs", self.OTHER_PAGE, ["Fluorescent Orange Matte", "Large"]))

    def test_a_colour_already_confirmed_here_is_still_off_page(self):
        self.assertTrue(self._index()._off_page(
            "1", "rs", self.OTHER_PAGE, ["Uranium Black Matte", "Large"]))

    def test_a_listing_with_no_options_keeps_the_old_demotion(self):
        self.assertTrue(self._index()._off_page("1", "rs", self.OTHER_PAGE, []))


class CrawlTokenCullTests(unittest.TestCase):
    """The frequency cull must not take an item's last hunted token."""

    def test_an_items_only_token_is_spared(self):
        from app.services.price_intelligence.connectors import CrawlTargets
        targets = CrawlTargets(brand_names=["Trek"],
                               model_tokens={"madone", "supersix", "evo"},
                               item_tokens=[{"madone"}, {"supersix", "evo"}])
        # A store full of Madone accessories makes both frequent.
        self.assertEqual({"madone"}, targets.tokens_to_keep({"madone", "evo"}))

    def test_a_multi_token_model_survives_losing_one(self):
        from app.services.price_intelligence.connectors import CrawlTargets
        targets = CrawlTargets(model_tokens={"supersix", "evo"},
                               item_tokens=[{"supersix", "evo"}])
        self.assertEqual(set(), targets.tokens_to_keep({"evo"}))


if __name__ == "__main__":
    unittest.main()
