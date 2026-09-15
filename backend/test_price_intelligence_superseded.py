"""'superseded' links: parked candidates, not tombstones.

A (item, competitor) pair holds one confirmed link. Every other pending candidate
for that pair used to stay in the review queue forever — confirming one returned
"already linked, skipped", and rejecting it would tombstone the listing for every
variant (rejected_keys is checked before any tier in matcher.match). Live, 13 of
54 pending rows were in that state, and the same item showed up to four times
against one store because the pre-2026-09-10 attribute logic anchored every
colourway of a model to one variant.

These tests pin the two rules that make the new status safe:
  1. set-aside/restore never write decided_by (the verdict MERGE guards on it —
     a restored row carrying an actor would be re-sent to the LLM every night);
  2. nothing here widens rejected_keys.
"""
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))

from app.services.price_intelligence import match_verifier, repository


def _params(call):
    return {
        p.name: (p.values if hasattr(p, "values") else p.value)
        for p in call.kwargs["job_config"].query_parameters
    }


def _sql(call):
    return call.args[0]


class SupersedeOnConfirmTests(unittest.TestCase):
    ROW = {"item_id": "i1", "competitor_id": "c1", "competitor_title": "Road Bike",
           "variant_options_json": None, "a1": "Blue", "a2": "56", "a3": None}

    def _confirm(self, rows, replace=False, affected=2):
        client = MagicMock()
        client.query.return_value.num_dml_affected_rows = affected
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows", side_effect=rows), \
             patch.object(repository, "get_bq_client", return_value=client), \
             patch.object(repository, "invalidate_pi_caches"):
            return repository.confirm_link("l1", replace=replace), client

    def test_confirm_sets_aside_other_pending_rows_for_the_pair(self):
        result, client = self._confirm([[self.ROW], []])
        self.assertEqual({"status": "confirmed", "replaced": 0, "superseded": 2}, result)
        self.assertEqual(2, client.query.call_count)
        confirm_sql = _sql(client.query.call_args_list[0])
        self.assertIn("SET status = @status", confirm_sql)
        park = client.query.call_args_list[1]
        self.assertIn("status = 'superseded'", _sql(park))
        self.assertIn("WHERE status = 'pending'", _sql(park))
        self.assertNotIn("decided_by", _sql(park))
        params = _params(park)
        self.assertEqual(["i1|c1"], params["pairs"])
        self.assertEqual(["l1"], params["exclude_ids"])
        self.assertEqual(repository.SUPERSEDED_NOTE, params["note"])

    def test_confirm_skipped_without_replace_writes_nothing(self):
        result, client = self._confirm([[self.ROW], [{"link_id": "old"}]])
        self.assertEqual("skipped", result["status"])
        self.assertTrue(result["can_replace"])
        client.query.assert_not_called()

    def test_confirm_guard_reads_variant_options_json_before_the_title(self):
        """SmartEtailing rows carry a bare model title; the colour/size lives
        only in the structured options, which the guard used to ignore."""
        row = dict(self.ROW, competitor_title="Ventral MIPS",
                   variant_options_json='["Neon Yellow", "XXL"]')
        prior = [{"status": "pending", "item_id": "i1", "competitor_id": "c1"}]
        result, client = self._confirm([[row], prior])
        self.assertEqual("rejected", result["status"])
        self.assertEqual(1, client.query.call_count)

    def test_partial_colour_agreement_is_not_a_conflict(self):
        """Their single-colour listing against our two-tone item shares a colour
        word: reviewable, and confirmable if the human says so."""
        row = dict(self.ROW, competitor_title="Ventral MIPS",
                   variant_options_json='["Hydrogen White Matt", "Medium"]',
                   a1="Hydrogen White/Uranium Black Matt w/Logo", a2="Medium")
        result, _ = self._confirm([[row], []])
        self.assertEqual("confirmed", result["status"])


class ReconcileSupersededTests(unittest.TestCase):
    PARK = [{"link_id": "p1", "item_id": "i1", "competitor_id": "c1",
             "competitor_title": "x", "source": "attr"}]
    RESTORE = [{"link_id": "s1", "item_id": "i2", "competitor_id": "c1",
                "competitor_title": "y", "source": "llm"}]

    def _reconcile(self, apply):
        client = MagicMock()
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows", side_effect=[self.PARK, self.RESTORE]), \
             patch.object(repository, "get_bq_client", return_value=client), \
             patch.object(repository, "invalidate_pi_caches"):
            return repository.reconcile_superseded_links(apply=apply), client

    def test_dry_run_reports_without_dml(self):
        report, client = self._reconcile(apply=False)
        client.query.assert_not_called()
        self.assertEqual(1, report["superseded"])
        self.assertEqual(1, report["restored"])
        self.assertEqual("p1", report["supersede_samples"][0]["link_id"])
        self.assertEqual("s1", report["restore_samples"][0]["link_id"])

    def test_apply_guards_each_direction_on_the_current_status(self):
        report, client = self._reconcile(apply=True)
        self.assertTrue(report["applied"])
        self.assertEqual(2, client.query.call_count)
        park, restore = client.query.call_args_list
        self.assertIn("SET status = 'superseded'", _sql(park))
        self.assertIn("IN UNNEST(@ids) AND status = 'pending'", _sql(park))
        self.assertEqual(["p1"], _params(park)["ids"])
        self.assertIn("SET status = 'pending'", _sql(restore))
        self.assertIn("AND status = 'superseded'", _sql(restore))
        self.assertIn("REPLACE(", _sql(restore))
        self.assertEqual(["s1"], _params(restore)["ids"])
        for call in (park, restore):
            self.assertNotIn("decided_by", _sql(call))

    def test_the_pair_join_coalesces_a_missing_competitor(self):
        captured = []

        def fake_rows(sql, params=None):
            captured.append(sql)
            return []

        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows", side_effect=fake_rows):
            repository.reconcile_superseded_links(apply=False)
        self.assertIn("COALESCE(c.competitor_id, '') = COALESCE(p.competitor_id, '')",
                      captured[0])
        self.assertIn("p.item_id IS NOT NULL", captured[0])
        self.assertIn("c.link_id IS NULL", captured[1])


class RestoreOnRejectTests(unittest.TestCase):
    def _reject(self, rows):
        client = MagicMock()
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows", side_effect=rows), \
             patch.object(repository, "get_bq_client", return_value=client), \
             patch.object(repository, "invalidate_pi_caches"):
            repository.decide_link("x", "rejected")
        return client

    def test_rejecting_a_confirmed_link_restores_its_pairs_set_aside_rows(self):
        client = self._reject([
            [{"status": "confirmed", "item_id": "i1", "competitor_id": "c1"}],
            [{"link_id": "s1", "item_id": "i1", "competitor_id": "c1"}],
        ])
        self.assertEqual(2, client.query.call_count)
        restore = client.query.call_args_list[1]
        self.assertIn("SET status = 'pending'", _sql(restore))
        self.assertNotIn("decided_by", _sql(restore))
        self.assertEqual(["s1"], _params(restore)["ids"])

    def test_rejecting_a_pending_link_does_not_restore(self):
        client = self._reject([
            [{"status": "pending", "item_id": "i1", "competitor_id": "c1"}],
        ])
        self.assertEqual(1, client.query.call_count)

    def test_confirming_never_looks_up_the_prior_status(self):
        client = MagicMock()
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows") as rows, \
             patch.object(repository, "get_bq_client", return_value=client), \
             patch.object(repository, "invalidate_pi_caches"):
            repository.decide_link("x", "confirmed")
        rows.assert_not_called()
        self.assertEqual(1, client.query.call_count)


class VerdictMergeGuardTests(unittest.TestCase):
    def test_verdict_merge_skips_rows_no_longer_pending(self):
        """A human can confirm (and set siblings aside) while an LLM batch is in
        flight; the verifier's snapshot must not write those rows back."""
        client = MagicMock()
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "get_bq_client", return_value=client), \
             patch.object(repository, "invalidate_pi_caches"):
            repository.update_link_verdicts([{"link_id": "l1", "status": "pending"}])
        self.assertIn("T.status = 'pending'", _sql(client.query.call_args))
        self.assertIn("T.decided_by IS NULL", _sql(client.query.call_args))


class RejectSurfacesTests(unittest.TestCase):
    def test_domain_sweep_rejects_set_aside_rows_too(self):
        """A pinned URL is the sole truth at its store: parked rows there must
        not survive to be restored into the queue later."""
        client = MagicMock()
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "get_bq_client", return_value=client), \
             patch.object(repository, "_rows", return_value=[]), \
             patch.object(repository, "get_link_match_keys", return_value=set()), \
             patch.object(repository, "invalidate_pi_caches"):
            repository.sweep_domain_for_item("i1", "https://steedcycles.com/p/1")
        self.assertIn("status IN ('confirmed', 'pending', 'superseded')",
                      _sql(client.query.call_args_list[0]))


class ReanchorPendingTests(unittest.TestCase):
    """The live shape: The Bike Zone's "Ventral MIPS" page carries one barcode per
    colourway, and every single-colour variant had been proposed against our
    two-tone "w/Logo" item because they share a colour word — while the exact
    sibling sat unmatched in the same matrix."""

    def _tracked(self, item_id, colour, size):
        return {"item_id": item_id, "title": "POC Ventral Air Mips Helmet",
                "brand": "POC", "sku": f"s{item_id}", "upc_normalized": None,
                "item_matrix_id": "7087", "matrix_description": "POC Ventral Air Mips",
                "attribute_1": colour, "attribute_2": size, "attribute_3": None,
                "archived": False}

    TRACKED_SPEC = [
        ("128286", "Hydrogen White/Uranium Black Matt w/Logo", "Medium"),
        ("112024", "Hydrogen White Matt", "Medium"),
        ("112028", "Uranium Black Matt", "Medium"),
        ("112025", "Hydrogen White Matt", "Large"),
    ]

    def _pending(self, link_id, options, item_id="128286"):
        return {"link_id": link_id, "item_id": item_id, "competitor_id": "bikezone",
                "status": "pending", "competitor_title": "Ventral MIPS",
                "variant_options_json": json.dumps(options), "source": "attr",
                "item_attribute_1": "Hydrogen White/Uranium Black Matt w/Logo",
                "item_attribute_2": "Medium", "item_attribute_3": None}

    def _run(self, pending, confirmed=(), apply=False):
        tracked = [self._tracked(*spec) for spec in self.TRACKED_SPEC]
        client = MagicMock()
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "get_product_links",
                          side_effect=[pending, list(confirmed)]), \
             patch.object(repository, "get_tracked_products", return_value=tracked), \
             patch.object(repository, "get_bq_client", return_value=client), \
             patch.object(repository, "invalidate_pi_caches"):
            return repository.reanchor_pending_links(apply=apply), client

    def test_an_exact_sibling_fit_moves_the_row_there(self):
        report, client = self._run([self._pending("a", ["Hydrogen White Matt", "Medium"])])
        self.assertEqual(1, report["moved"])
        self.assertEqual(0, report["moved_and_superseded"])
        sample = report["samples"][0]
        self.assertEqual(("128286", "112024", "pending"),
                         (sample["from_item_id"], sample["to_item_id"], sample["as_status"]))
        client.query.assert_not_called()

    def test_a_move_onto_a_confirmed_pair_is_set_aside(self):
        confirmed = [{"link_id": "c", "item_id": "112024", "competitor_id": "bikezone",
                      "status": "confirmed"}]
        report, _ = self._run([self._pending("a", ["Hydrogen White Matt", "Medium"])],
                              confirmed)
        self.assertEqual(0, report["moved"])
        self.assertEqual(1, report["moved_and_superseded"])
        self.assertEqual("superseded", report["samples"][0]["as_status"])

    def test_a_partial_fit_does_not_move(self):
        """Their two-tone colourway names no single sibling exactly — the best of
        a partial pool is a guess, and moving on guesses just churns."""
        report, _ = self._run([self._pending("b", ["Hydrogen White/Uranium Black Matt", "Medium"])])
        self.assertEqual(0, report["moved"] + report["moved_and_superseded"])

    def test_a_row_already_on_its_exact_variant_stays(self):
        report, _ = self._run([self._pending("c", ["Hydrogen White Matt", "Medium"],
                                             item_id="112024")])
        self.assertEqual(0, report["moved"] + report["moved_and_superseded"])

    def test_apply_moves_with_one_dml_carrying_statuses(self):
        confirmed = [{"link_id": "c", "item_id": "112028", "competitor_id": "bikezone",
                      "status": "confirmed"}]
        report, client = self._run(
            [self._pending("a", ["Hydrogen White Matt", "Medium"]),
             self._pending("b", ["Uranium Black Matt", "Medium"])],
            confirmed, apply=True)
        self.assertTrue(report["applied"])
        self.assertEqual(1, client.query.call_count)
        call = client.query.call_args
        self.assertIn("UNNEST(@statuses)", _sql(call))
        params = _params(call)
        self.assertEqual(["a", "b"], params["link_ids"])
        self.assertEqual(["112024", "112028"], params["new_ids"])
        self.assertEqual(["attr", "attr"], params["sources"])
        self.assertEqual(["", "superseded"], params["statuses"])
        self.assertEqual(repository.SUPERSEDED_NOTE, params["note"])


class VerifierSetAsideTests(unittest.TestCase):
    """The verifier used to demote a candidate on an already-confirmed pair back
    to pending with a note. With a verdict set it was never re-fetched, and the
    queue never hid it — the exact "stuck forever" row the human could neither
    confirm nor safely reject."""

    def _item(self, item_id, colour, size):
        return {"item_id": item_id, "title": "POC Amidal Helmet", "brand": "POC",
                "sku": f"s{item_id}", "upc_normalized": None, "item_matrix_id": "m",
                "matrix_description": "POC Amidal Helmet", "attribute_1": colour,
                "attribute_2": size, "attribute_3": None, "current_retail": 250.0}

    def _link(self, link_id, item_id, options, **extra):
        row = {"link_id": link_id, "item_id": item_id, "competitor_id": "wob",
               "status": "pending", "level": "variant", "confidence": 0.9,
               "competitor_title": "Amidal Cycling Helmet",
               "variant_options_json": json.dumps(options), "competitor_sku": "x",
               "their_price": 240.0, "competitor_url": "https://wob.ca/p/1",
               "source": "attr", "llm_verdict": None}
        row.update(extra)
        return row

    def _verify(self, pending, confirmed, verdicts):
        tracked = [self._item("1", "Uranium Black Matt", "M"),
                   self._item("2", "Uranium Black Matt", "L")]
        message = SimpleNamespace(
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            content=[SimpleNamespace(type="text", text=json.dumps({"results": verdicts}))])
        client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: message))
        written = []
        with patch.object(match_verifier.config, "ANTHROPIC_API_KEY", "test-key"), \
             patch.object(repository, "get_product_links", side_effect=[pending, confirmed]), \
             patch.object(repository, "get_tracked_products", return_value=tracked), \
             patch.object(repository, "update_link_verdicts", side_effect=written.extend), \
             patch.object(match_verifier.settings, "get", return_value=False), \
             patch.object(match_verifier, "_get_anthropic_client", return_value=client):
            stats = match_verifier.verify_candidates()
        return stats, {u["link_id"]: u for u in written}

    def test_a_same_variant_verdict_on_a_confirmed_pair_is_set_aside_not_demoted(self):
        confirmed = [{"link_id": "c", "item_id": "1", "competitor_id": "wob",
                      "competitor_url": "https://wob.ca/p/1"}]
        stats, updates = self._verify(
            [self._link("a", "1", ["Uranium Black Matt", "M"])], confirmed,
            [{"pair_id": "a", "verdict": "same_variant", "competitor_size": "M",
              "reason": "same"}])
        self.assertEqual("superseded", updates["a"]["status"])
        self.assertIn(repository.SUPERSEDED_NOTE, updates["a"]["llm_reason"])
        self.assertEqual(0.9, updates["a"]["confidence"])  # not competing: no penalty
        self.assertEqual(1, stats["superseded"])
        self.assertEqual(0, stats["pending"])
        self.assertNotIn("decided_by", updates["a"])

    def test_a_same_model_reanchor_onto_a_confirmed_pair_is_set_aside(self):
        confirmed = [{"link_id": "c", "item_id": "2", "competitor_id": "wob",
                      "competitor_url": "https://wob.ca/p/1"}]
        stats, updates = self._verify(
            [self._link("b", "1", ["Uranium Black Matt", "L"])], confirmed,
            [{"pair_id": "b", "verdict": "same_model", "competitor_size": "L",
              "reason": "same model, size L"}])
        self.assertEqual("2", updates["b"]["item_id"])
        self.assertEqual("superseded", updates["b"]["status"])
        self.assertEqual(1, stats["superseded"])

    def test_an_in_run_loser_still_stays_pending(self):
        """Two candidates for an UNCONFIRMED pair still compete; the loser stays
        reviewable — a human may disagree about which variant won."""
        stats, updates = self._verify(
            [self._link("a", "1", ["Uranium Black Matt", "M"]),
             self._link("b", "1", ["Uranium Black", "M"])], [],
            [{"pair_id": "a", "verdict": "same_variant", "competitor_size": "M", "reason": "x"},
             {"pair_id": "b", "verdict": "same_variant", "competitor_size": "M", "reason": "y"}])
        self.assertEqual({"pending"}, {u["status"] for u in updates.values()})
        self.assertIn("closer variant match", updates["b"]["llm_reason"])
        self.assertEqual(2, stats["pending"])
        self.assertEqual(0, stats["superseded"])


if __name__ == "__main__":
    unittest.main()
