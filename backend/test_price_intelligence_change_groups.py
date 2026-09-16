"""Product × store rollup of price-change events (change_groups).

Pure module, so these run without BigQuery. Pinned down: matrix identity comes
from the tracked row (events carry none), variant counts read "N of M" only
when partial, the latest event per listing drives the summary while every
event stays in event_ids, filters apply per event before grouping, and the
Slack rendering never produces a section that slack.section() would truncate.
"""
import unittest

from app.services.price_intelligence import change_groups as cg


def _tracked(item_id, matrix=None, title="Widget", brand="Acme", retail=100.0,
             attr=None, archived=False):
    return {
        "item_id": item_id, "item_matrix_id": matrix,
        "matrix_description": "SuperSix EVO 5" if matrix else None,
        "title": title, "brand": brand, "current_retail": retail,
        "attribute_1": attr, "attribute_2": None, "attribute_3": None,
        "archived": archived,
    }


def _event(event_id, item_id, competitor="c1", kind="price_increase",
           old=100.0, new=120.0, pct=20.0, url="https://store.example/p",
           at="2026-09-16T02:00:00", acked=False, title=None):
    return {
        "event_id": event_id, "event_type": kind, "item_id": item_id,
        "competitor_id": competitor, "competitor_name": "Primeau Velo",
        "item_title": title or f"SuperSix EVO 5 ({item_id})", "item_brand": "Cannondale",
        "old_price": old, "new_price": new, "pct_change": pct, "url": url,
        "occurred_at": at, "acknowledged": acked,
    }


MATRIX = {
    "1": _tracked("1", "m1", "SuperSix EVO 5", "Cannondale", 2999.0, "54cm"),
    "2": _tracked("2", "m1", "SuperSix EVO 5", "Cannondale", 2999.0, "56cm"),
    "3": _tracked("3", "m1", "SuperSix EVO 5", "Cannondale", 3099.0, "58cm"),
}


class GroupingTests(unittest.TestCase):
    def test_matrix_variants_roll_up_to_one_group_per_store(self):
        events = [_event("e1", "1", old=2666.61, new=3199.93, pct=20.0),
                  _event("e2", "2", old=2666.61, new=3199.93, pct=20.0),
                  _event("e3", "3", old=2666.61, new=3199.93, pct=20.0)]
        groups = cg.group_price_changes(events, MATRIX)
        self.assertEqual(1, len(groups))
        g = groups[0]
        self.assertEqual("matrix", g["product_kind"])
        self.assertEqual("m1", g["item_matrix_id"])
        self.assertEqual("Cannondale SuperSix EVO 5", g["title"])
        self.assertEqual(3, g["variants_changed"])
        self.assertEqual(3, g["variants_total"])
        self.assertEqual("increase", g["direction"])
        self.assertEqual((20.0, 20.0), (g["pct_min"], g["pct_max"]))
        self.assertEqual((3199.93, 3199.93), (g["new_price_min"], g["new_price_max"]))
        self.assertEqual((2999.0, 3099.0), (g["our_price_min"], g["our_price_max"]))
        self.assertEqual(["e1", "e2", "e3"], sorted(g["event_ids"]))

    def test_same_matrix_at_two_stores_is_two_groups(self):
        events = [_event("e1", "1", competitor="c1"), _event("e2", "2", competitor="c2")]
        keys = {g["group_key"] for g in cg.group_price_changes(events, MATRIX)}
        self.assertEqual({"matrix:m1|c:c1", "matrix:m1|c:c2"}, keys)

    def test_partial_matrix_counts_changed_of_total(self):
        events = [_event("e1", "1"), _event("e2", "2")]
        g = cg.group_price_changes(events, MATRIX)[0]
        self.assertEqual((2, 3), (g["variants_changed"], g["variants_total"]))

    def test_archived_variants_do_not_count_toward_total(self):
        tracked = dict(MATRIX)
        tracked["4"] = _tracked("4", "m1", "SuperSix EVO 5", "Cannondale", archived=True)
        g = cg.group_price_changes([_event("e1", "1")], tracked)[0]
        self.assertEqual(3, g["variants_total"])

    def test_two_listings_for_one_item_count_one_variant(self):
        # Catalog crawl + confirmed link can both emit for the same item×store.
        events = [_event("e1", "1", url="https://store.example/a"),
                  _event("e2", "1", url="https://store.example/b", at="2026-09-16T02:05:00")]
        g = cg.group_price_changes(events, MATRIX)[0]
        self.assertEqual(1, g["variants_changed"])
        self.assertEqual(["e2", "e1"], g["event_ids"])  # newest first

    def test_standalone_item_and_unmatched_listing(self):
        tracked = {"9": _tracked("9", None, "Grand Prix 5000 700x28", "Continental", 64.99)}
        events = [_event("e1", "9", kind="price_drop", old=69.99, new=63.99, pct=-8.57),
                  _event("e2", None, kind="price_drop", old=10.0, new=9.0, pct=-10.0,
                         url="https://store.example/orphan", title="Some Orphan (Red)")]
        groups = {g["product_kind"]: g for g in cg.group_price_changes(events, tracked)}
        item = groups["item"]
        self.assertEqual("Continental Grand Prix 5000 700x28", item["title"])
        self.assertEqual("9", item["item_id"])
        self.assertIsNone(item["variants_total"])
        listing = groups["listing"]
        self.assertEqual("listing:https://store.example/orphan|c:c1", listing["group_key"])
        # Unknown to the tracked table: the event title stays verbatim.
        self.assertEqual("Cannondale Some Orphan (Red)", listing["title"])

    def test_missing_tracked_row_keeps_event_title_verbatim(self):
        g = cg.group_price_changes([_event("e1", "77", title="Old Thing (XL)")], {})[0]
        self.assertEqual("item", g["product_kind"])
        self.assertEqual("Cannondale Old Thing (XL)", g["title"])
        self.assertIsNone(g["our_price_min"])

    def test_brand_is_not_doubled(self):
        tracked = {"5": _tracked("5", None, "Cannondale Topstone 3", "Cannondale")}
        g = cg.group_price_changes([_event("e1", "5")], tracked)[0]
        self.assertEqual("Cannondale Topstone 3", g["title"])

    def test_latest_event_per_listing_drives_the_summary(self):
        # 100 → 90 → 95 over two runs is a net drop, not "mixed".
        events = [_event("e1", "1", kind="price_drop", old=100, new=90, pct=-10,
                         at="2026-09-15T02:00:00"),
                  _event("e2", "1", kind="price_increase", old=90, new=95, pct=5.56,
                         at="2026-09-16T02:00:00")]
        g = cg.group_price_changes(events, MATRIX)[0]
        self.assertEqual("increase", g["direction"])
        self.assertEqual(95.0, g["new_price_max"])
        self.assertEqual(["e2", "e1"], g["event_ids"])
        self.assertEqual("2026-09-15T02:00:00", g["first_occurred_at"])
        self.assertEqual("2026-09-16T02:00:00", g["last_occurred_at"])

    def test_mixed_direction_and_headline_url(self):
        events = [_event("e1", "1", kind="price_drop", old=100, new=96, pct=-4,
                         url="https://store.example/small"),
                  _event("e2", "2", kind="price_increase", old=100, new=106, pct=6,
                         url="https://store.example/big")]
        g = cg.group_price_changes(events, MATRIX)[0]
        self.assertEqual("mixed", g["direction"])
        self.assertEqual((-4.0, 6.0), (g["pct_min"], g["pct_max"]))
        self.assertEqual(6.0, g["pct_abs_max"])
        self.assertEqual("https://store.example/big", g["url"])

    def test_direction_and_min_pct_filter_events_before_grouping(self):
        events = [_event("e1", "1", kind="price_drop", old=100, new=96, pct=-4),
                  _event("e2", "2", kind="price_increase", old=100, new=106, pct=6),
                  _event("e3", "3", kind="price_increase", old=100, new=101, pct=1)]
        drops = cg.group_price_changes(events, MATRIX, directions="drops")
        self.assertEqual(["e1"], drops[0]["event_ids"])
        self.assertEqual("drop", drops[0]["direction"])
        big = cg.group_price_changes(events, MATRIX, min_abs_pct=3)
        self.assertEqual(["e1", "e2"], sorted(big[0]["event_ids"]))
        self.assertEqual(2, big[0]["variants_changed"])

    def test_unknown_direction_choice_widens_to_both(self):
        self.assertEqual(cg.PRICE_EVENT_TYPES, cg.direction_event_types("garbage"))
        self.assertEqual(("price_drop",), cg.direction_event_types("drops"))

    def test_non_price_events_are_ignored(self):
        events = [_event("e1", "1", kind="map_violation"), _event("e2", "1", kind="out_of_stock")]
        self.assertEqual([], cg.group_price_changes(events, MATRIX))

    def test_pct_none_sorts_last_and_reads_as_zero(self):
        events = [_event("e1", "1", old=0, new=50, pct=None),
                  _event("e2", "2", competitor="c2", old=100, new=105, pct=5)]
        groups = cg.group_price_changes(events, MATRIX, min_abs_pct=0)
        self.assertEqual(["c2", "c1"], [g["competitor_id"] for g in groups])
        self.assertIsNone(groups[1]["pct_min"])
        self.assertEqual(0.0, groups[1]["pct_abs_max"])
        # Below any positive threshold it disappears, like the SQL's COALESCE(pct, 0).
        self.assertEqual(1, len(cg.group_price_changes(events, MATRIX, min_abs_pct=1)))

    def test_sort_is_magnitude_then_breadth(self):
        events = [_event("e1", "1", pct=5, old=100, new=105),
                  _event("e2", "9", competitor="c2", pct=5, old=100, new=105),
                  _event("e3", "8", competitor="c3", pct=-30, old=100, new=70, kind="price_drop")]
        tracked = dict(MATRIX)
        tracked["9"] = _tracked("9", None, "Solo", "Acme")
        tracked["8"] = _tracked("8", None, "Big drop", "Acme")
        events.append(_event("e4", "2", pct=5, old=100, new=105))  # widens the c1 matrix group
        got = [g["competitor_id"] for g in cg.group_price_changes(events, tracked)]
        self.assertEqual(["c3", "c1", "c2"], got)

    def test_unread_count_and_variant_labels(self):
        events = [_event("e1", "1", acked=True), _event("e2", "2")]
        g = cg.group_price_changes(events, MATRIX)[0]
        self.assertEqual(1, g["unread_count"])
        self.assertFalse(g["acknowledged"])
        self.assertEqual({"54cm", "56cm"}, {ev["variant"] for ev in g["events"]})
        # Variant label falls back to the event title's suffix without a tracked row.
        g2 = cg.group_price_changes([_event("e9", "77", title="Thing (XL / Red)")], {})[0]
        self.assertEqual("XL / Red", g2["events"][0]["variant"])


class FormattingTests(unittest.TestCase):
    def test_users_example_line(self):
        events = [_event(f"e{i}", str(i), old=2666.61, new=3199.93, pct=20.0)
                  for i in (1, 2, 3)]
        g = cg.group_price_changes(events, MATRIX)[0]
        self.assertEqual(
            "🔺 *Cannondale SuperSix EVO 5* (3 variants) +20% → $3,199.93 (was $2,666.61)"
            " — Primeau Velo · ours $2,999.00–$3,099.00 · <https://store.example/p|view>",
            cg.format_group_line(g))

    def test_partial_matrix_ranges_and_drop_arrow(self):
        events = [_event("e1", "1", kind="price_drop", old=372.0, new=349.0, pct=-6.2),
                  _event("e2", "2", kind="price_drop", old=405.0, new=389.0, pct=-4.0)]
        line = cg.format_group_line(cg.group_price_changes(events, MATRIX)[0])
        self.assertTrue(line.startswith("🔻 *Cannondale SuperSix EVO 5* (2 of 3 variants) "
                                        "-4–6.2% → $349.00–$389.00 (was $372.00–$405.00)"), line)

    def test_mixed_range_and_omitted_tokens(self):
        events = [_event("e1", "1", kind="price_drop", old=None, new=96, pct=None,
                         url="https://x|y"),
                  _event("e2", "2", kind="price_increase", old=100, new=106, pct=6,
                         url="https://x|y")]
        g = cg.group_price_changes(events, {"1": _tracked("1", "m1", retail=None),
                                            "2": _tracked("2", "m1", retail=None)})
        line = cg.format_group_line(g[0])
        self.assertIn("↕️", line)
        self.assertIn("+6% → $96.00–$106.00 (was $100.00)", line)
        self.assertNotIn("ours", line)
        self.assertNotIn("|view>", line)  # a URL containing | would break the link

    def test_single_item_has_no_variant_suffix(self):
        tracked = {"9": _tracked("9", None, "Grand Prix 5000 700x28", "Continental", 64.99)}
        g = cg.group_price_changes(
            [_event("e1", "9", kind="price_drop", old=69.99, new=63.99, pct=-8.57)], tracked)[0]
        self.assertEqual(
            "🔻 *Continental Grand Prix 5000 700x28* -8.6% → $63.99 (was $69.99)"
            " — Primeau Velo · ours $64.99 · <https://store.example/p|view>",
            cg.format_group_line(g))

    def test_title_is_escaped_and_capped(self):
        long_title = "A" * 100 + " <b> & co"
        g = cg.group_price_changes([_event("e1", "77", title=long_title)], {})[0]
        line = cg.format_group_line(g)
        self.assertNotIn("<b>", line)
        # Brand prefix + 80-char cap with an ellipsis.
        self.assertIn("*Cannondale " + "A" * 68 + "…*", line)

    def test_pct_formatting(self):
        self.assertEqual("+20%", cg.fmt_pct(20))
        self.assertEqual("-8.6%", cg.fmt_pct(-8.57))
        self.assertEqual("", cg.fmt_pct(None))
        self.assertEqual("-3.2–5.1%", cg._pct_range(-5.1, -3.2))
        self.assertEqual("+3–5%", cg._pct_range(3, 5))
        self.assertEqual("-4% to +6%", cg._pct_range(-4, 6))
        self.assertEqual("+20%", cg._pct_range(20.0, 20.04))


class MessageTests(unittest.TestCase):
    def _groups(self, n, competitor="c1"):
        tracked, events = {}, []
        for i in range(n):
            tracked[str(i)] = _tracked(str(i), None, f"Item {i} " + "x" * 60, "Acme")
            events.append(_event(f"e{i}", str(i), competitor=f"{competitor}{i}",
                                 pct=float(i + 1), old=100, new=100 + i + 1,
                                 url=f"https://store.example/{i}"))
        return cg.group_price_changes(events, tracked)

    def test_message_shape_and_coverage(self):
        groups = self._groups(3)
        text, blocks, covered = cg.build_price_change_message(groups, 15, "Sep 16")
        self.assertEqual("Competitor price changes: 3 products", text)
        self.assertEqual("header", blocks[0]["type"])
        self.assertIn("Sep 16", blocks[0]["text"]["text"])
        self.assertEqual("context", blocks[1]["type"])
        self.assertIn("3 changes · 3 products · 3 stores · 0▼ 3▲", blocks[1]["elements"][0]["text"])
        self.assertEqual("section", blocks[2]["type"])
        self.assertEqual(3, len(blocks))
        self.assertEqual(["e0", "e1", "e2"], sorted(covered))

    def test_cap_folds_the_rest_into_an_overflow_line_but_still_covers_them(self):
        groups = self._groups(20)
        _, blocks, covered = cg.build_price_change_message(groups, 5, "Sep 16")
        self.assertEqual(5, blocks[2]["text"]["text"].count("\n") + 1)
        self.assertIn("…and 15 more", blocks[-1]["elements"][0]["text"])
        self.assertEqual(20, len(covered))

    def test_sections_never_approach_the_slack_limit(self):
        groups = self._groups(50)
        _, blocks, _ = cg.build_price_change_message(groups, 50, "Sep 16")
        sections = [b for b in blocks if b["type"] == "section"]
        self.assertGreater(len(sections), 1)
        for b in sections:
            self.assertLessEqual(len(b["text"]["text"]), 2900)
        self.assertLessEqual(len(blocks), 50)
        # Every line landed in exactly one section.
        self.assertEqual(50, sum(b["text"]["text"].count("\n") + 1 for b in sections))

    def test_zero_groups_renders_nothing_useful(self):
        text, blocks, covered = cg.build_price_change_message([], 15, "Sep 16")
        self.assertEqual([], covered)
        self.assertEqual(2, len(blocks))


if __name__ == "__main__":
    unittest.main()
