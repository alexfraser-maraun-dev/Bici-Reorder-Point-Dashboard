"""Slack dispatch gating (notify.dispatch_run) and the settings it reads.

No BigQuery and no network: repository reads and the Slack poster are mocked,
settings.get is driven from a dict. What matters here is which messages fire
under which switches, that a Slack-muted store reaches none of them, and that
`notified` is stamped on exactly the events a message covered.
"""
import unittest
from unittest.mock import patch

from app.services.notifications import slack
from app.services.price_intelligence import notify, repository, scrape_runner, settings

DEFAULTS = {
    "slack_enabled": True,
    "slack_webhook_url": "https://hooks.slack.com/main",
    "slack_alerts_webhook_url": "",
    "slack_health_alerts": True,
    "slack_map_pings": True,
    "slack_undercut_pings": False,
    "slack_max_priority_pings": 15,
    "slack_send_digest": True,
    "digest_enabled": True,
    "slack_price_changes": True,
    "slack_price_change_min_pct": 0,
    "slack_price_change_directions": "both",
    "slack_max_price_changes": 15,
}

TRACKED = [
    {"item_id": "1", "item_matrix_id": "m1", "matrix_description": "SuperSix EVO 5",
     "brand": "Cannondale", "title": "SuperSix EVO 5", "attribute_1": "54cm",
     "current_retail": 2999.0, "map_price": None, "is_map": False},
    {"item_id": "2", "item_matrix_id": "m1", "matrix_description": "SuperSix EVO 5",
     "brand": "Cannondale", "title": "SuperSix EVO 5", "attribute_1": "56cm",
     "current_retail": 2999.0, "map_price": None, "is_map": False},
    {"item_id": "9", "item_matrix_id": None, "title": "Helmet", "brand": "Acme",
     "current_retail": 109.0, "map_price": 105.0, "is_map": True},
]


def _ev(event_id, kind, item_id, competitor="c1", pct=20.0, old=100.0, new=120.0):
    return {"event_id": event_id, "event_type": kind, "item_id": item_id,
            "competitor_id": competitor, "competitor_name": f"Store {competitor}",
            "item_title": "x", "item_brand": None, "old_price": old, "new_price": new,
            "pct_change": pct, "url": f"https://{competitor}.example/p",
            "occurred_at": "2026-09-16T02:00:00", "acknowledged": False}


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.settings = dict(DEFAULTS)
        self.posts, self.alerts = [], []
        self.marked = []
        self.events = [
            _ev("map1", "map_violation", "9", pct=-18.0, old=109.0, new=89.0),
            _ev("uc1", "undercut", "1", competitor="c2", pct=-5.0, old=3100.0, new=2900.0),
            _ev("p1", "price_increase", "1"),
            _ev("p2", "price_increase", "2"),
            _ev("p3", "price_drop", "9", competitor="c2", pct=-8.0, old=100.0, new=92.0),
        ]
        self.muted = set()
        self.digest_row = {"digest_md": "## Market\nfine"}

        patches = [
            patch.object(settings, "get", side_effect=lambda k: self.settings[k]),
            patch.object(slack, "post", side_effect=lambda hook, **kw: self.posts.append((hook, kw)) or True),
            patch.object(slack, "post_alert", side_effect=lambda hook, **kw: self.alerts.append((hook, kw)) or True),
            patch.object(repository, "get_change_events", side_effect=self._load),
            patch.object(repository, "get_tracked_products", return_value=TRACKED),
            patch.object(repository, "slack_muted_competitor_ids", side_effect=lambda: self.muted),
            patch.object(repository, "get_digest_for_run", side_effect=lambda run_id: self.digest_row),
            patch.object(repository, "mark_events_notified", side_effect=self.marked.extend),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.load_kwargs = None

    def _load(self, **kwargs):
        self.load_kwargs = kwargs
        return list(self.events)

    def _run(self, status="success"):
        notify.dispatch_run("run-1", status, {"changes": 5}, [])

    def _post_texts(self):
        return [kw.get("text") for _, kw in self.posts]

    def test_default_run_posts_map_ping_digest_and_price_changes(self):
        self._run()
        # Load asks only for the Slack-relevant families with the wide cap.
        self.assertEqual(["price_drop", "price_increase", "map_violation", "undercut"],
                         self.load_kwargs["event_types"])
        self.assertEqual(3000, self.load_kwargs["limit"])
        self.assertEqual("run-1", self.load_kwargs["run_id"])
        # One MAP ping, no undercut (off by default).
        self.assertEqual(1, len(self.alerts))
        self.assertIn("MAP violation", self.alerts[0][1]["title"])
        self.assertIn("Our MAP floor: $105.00", self.alerts[0][1]["body"])
        # Digest first, then the price-changes rollup, both on the main hook.
        self.assertEqual(["Price intel digest", "Competitor price changes: 2 products"],
                         self._post_texts())
        self.assertTrue(all(h == "https://hooks.slack.com/main" for h, _ in self.posts))
        # notified = the ping + every event a price-change group covered.
        self.assertEqual({"map1", "p1", "p2", "p3"}, set(self.marked))

    def test_digest_message_has_no_notable_moves_block(self):
        self._run()
        digest_blocks = self.posts[0][1]["blocks"]
        self.assertEqual(2, len(digest_blocks))
        self.assertNotIn("Notable moves", str(digest_blocks))

    def test_slack_muted_store_reaches_no_message(self):
        self.muted = {"c2"}
        self.settings["slack_undercut_pings"] = True
        self._run()
        self.assertEqual(1, len(self.alerts))  # the c2 undercut is gone
        rollup = self.posts[1][1]["blocks"]
        self.assertNotIn("Store c2", str(rollup))
        self.assertEqual({"map1", "p1", "p2"}, set(self.marked))

    def test_undercut_pings_when_enabled_sort_after_map(self):
        self.settings["slack_undercut_pings"] = True
        self._run()
        titles = [kw["title"] for _, kw in self.alerts]
        self.assertEqual(2, len(titles))
        self.assertIn("MAP violation", titles[0])
        self.assertIn("Undercut", titles[1])
        self.assertIn("Our price: $2,999.00", self.alerts[1][1]["body"])
        self.assertEqual("#e8710a", self.alerts[1][1]["color"])

    def test_priority_cap_overflow_counts_both_families(self):
        self.settings["slack_undercut_pings"] = True
        self.settings["slack_max_priority_pings"] = 1
        self._run()
        self.assertEqual(1, len(self.alerts))
        self.assertIn("MAP violation", self.alerts[0][1]["title"])
        self.assertIn("1* more alerts", self._post_texts()[0])
        self.assertIn("uc1", self.marked)  # overflow is still marked notified

    def test_digest_disabled_skips_the_post_without_querying(self):
        self.settings["digest_enabled"] = False
        with patch.object(repository, "get_digest_for_run") as loader:
            self._run()
            loader.assert_not_called()
        self.assertEqual(["Competitor price changes: 2 products"], self._post_texts())

    def test_send_digest_off_keeps_price_changes(self):
        self.settings["slack_send_digest"] = False
        self._run()
        self.assertEqual(["Competitor price changes: 2 products"], self._post_texts())

    def test_price_changes_off_keeps_digest_and_pings(self):
        self.settings["slack_price_changes"] = False
        self._run()
        self.assertEqual(["Price intel digest"], self._post_texts())
        self.assertEqual(["map1"], self.marked)

    def test_min_pct_and_direction_settings_apply(self):
        self.settings["slack_price_change_min_pct"] = 10
        self._run()
        rollup = str(self.posts[1][1]["blocks"])
        self.assertIn("SuperSix", rollup)
        self.assertNotIn("Helmet", rollup)  # -8% drop is under the 10% floor
        self.assertNotIn("p3", self.marked)

        self.posts.clear(); self.marked.clear()
        self.settings["slack_price_change_min_pct"] = 0
        self.settings["slack_price_change_directions"] = "drops"
        self._run()
        rollup = str(self.posts[1][1]["blocks"])
        self.assertIn("Helmet", rollup)
        self.assertNotIn("SuperSix", rollup)

    def test_nothing_is_marked_when_the_post_fails(self):
        with patch.object(slack, "post", return_value=False), \
             patch.object(slack, "post_alert", return_value=False):
            self._run()
        self.assertEqual([], self.marked)

    def test_no_groups_means_no_price_change_message(self):
        self.events = [e for e in self.events if e["event_type"] == "map_violation"]
        self._run()
        self.assertEqual(["Price intel digest"], self._post_texts())

    def test_health_alert_still_fires_on_a_failed_run(self):
        self.events = []
        self.digest_row = None
        self._run(status="failed")
        self.assertIn("❌ Scrape failed", self._post_texts()[0])

    def test_disabled_slack_posts_nothing(self):
        self.settings["slack_enabled"] = False
        self._run(status="failed")
        self.assertEqual([], self.posts)
        self.assertEqual([], self.alerts)

    def test_send_test_reports_every_message(self):
        result = notify.send_test()
        self.assertTrue(result["sent"])
        self.assertEqual({"map_ping": True, "price_changes": True, "digest": True},
                         result["results"])
        self.settings["slack_undercut_pings"] = True
        self.assertIn("undercut_ping", notify.send_test()["results"])


class RunnerDigestGateTests(unittest.TestCase):
    def test_disabled_skips_generation_entirely(self):
        errors = []
        with patch.object(settings, "get", return_value=False), \
             patch("app.services.price_intelligence.digest.generate_digest") as gen, \
             patch.object(scrape_runner, "_set_status") as status:
            scrape_runner._maybe_generate_digest("run-1", errors)
        gen.assert_not_called()
        status.assert_not_called()
        self.assertEqual([], errors)

    def test_enabled_generates_and_records_failures_without_raising(self):
        errors = []
        with patch.object(settings, "get", return_value=True), \
             patch("app.services.price_intelligence.digest.generate_digest",
                   side_effect=RuntimeError("boom")), \
             patch.object(scrape_runner, "_set_status"):
            scrape_runner._maybe_generate_digest("run-1", errors)
        self.assertEqual(["digest: boom"], errors)


class SettingsChoiceKindTests(unittest.TestCase):
    def test_choice_rejects_values_outside_the_list(self):
        spec = settings._SPECS["slack_price_change_directions"]
        self.assertEqual("drops", settings._validate("slack_price_change_directions", spec, " drops "))
        with self.assertRaises(ValueError):
            settings._validate("slack_price_change_directions", spec, "sideways")
        with self.assertRaises(ValueError):
            settings._validate("slack_price_change_directions", spec, True)

    def test_bad_stored_choice_falls_back_to_the_default(self):
        with patch.object(repository, "get_settings_overrides",
                          return_value={"slack_price_change_directions": "sideways"}):
            self.assertEqual("both", settings.get("slack_price_change_directions"))

    def test_describe_exposes_choices(self):
        with patch.object(repository, "get_settings_overrides", return_value={}):
            by_key = {s["key"]: s for s in settings.describe()}
        self.assertEqual(["both", "drops", "increases"],
                         by_key["slack_price_change_directions"]["choices"])
        self.assertIsNone(by_key["slack_price_changes"]["choices"])
        self.assertNotIn("slack_max_digest_moves", by_key)
        self.assertTrue(by_key["digest_enabled"]["value"])
        self.assertFalse(by_key["slack_undercut_pings"]["value"])


if __name__ == "__main__":
    unittest.main()
