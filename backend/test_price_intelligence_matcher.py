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

from app.services.price_intelligence import config, matcher


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


if __name__ == "__main__":
    unittest.main()
