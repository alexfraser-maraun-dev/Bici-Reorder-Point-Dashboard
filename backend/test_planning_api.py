import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from fastapi import HTTPException

from app import main
from app.services.demand_planning import week_start
from app.services.lightspeed_gateway import FakeLightspeedGateway
from app.services.planning_service import clear_run_cache, create_planning_run
from app.services.planning_store import PlanningStore
import app.services.planning_store as planning_store_module


class PlanningApiContractTests(unittest.TestCase):
    def setUp(self):
        clear_run_cache()
        handle, self.db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        planning_store_module._store = PlanningStore(f"sqlite:///{self.db_path}")

    def tearDown(self):
        planning_store_module._store = None
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    @staticmethod
    def _run():
        end = week_start(date(2026, 7, 14)) - timedelta(days=7)
        history = [{
            "item_id": "10", "location_id": "3",
            "week_start": (end - timedelta(days=(105 - index) * 7)).isoformat(),
            "raw_units_sold": 2, "category_path": "Nutrition",
        } for index in range(106)]
        return create_planning_run(
            items=[{
                "item_id": "10", "location_id": "3", "sku": "GEL",
                "category": "Nutrition", "vendor_id": "55", "vendor": "Fuel Co",
                "landed_cost": 2.0, "current_qoh": 0,
            }],
            weekly_history=history,
            as_of_date=date(2026, 7, 14),
        )

    def test_selected_recommendation_draft_preview_is_read_only(self):
        run = self._run()
        rec = run["recommendations"][0]
        response = main.create_po_drafts_endpoint({
            "run_id": run["run_id"],
            "recommendation_ids": [rec["recommendation_id"]],
            "created_by": "buyer@example.com",
        })
        draft = response["data"][0]
        self.assertEqual(draft["lines"][0]["recommendation_id"], rec["recommendation_id"])
        approved = main.transition_po_draft_endpoint(draft["draft_id"], {
            "expected_version": draft["version"], "status": "approved",
        })["data"]
        fake = FakeLightspeedGateway([])
        with patch("app.services.lightspeed_gateway.LiveLightspeedReadGateway", return_value=fake):
            preview = main.preview_po_draft_endpoint(draft["draft_id"])["data"]
        self.assertFalse(preview["writes_performed"])
        self.assertEqual(fake.operations, [])
        self.assertEqual(preview["draft"]["status"], "previewed")
        self.assertEqual(approved["version"] + 1, preview["draft"]["version"])

    def test_blocked_recommendation_cannot_be_drafted(self):
        run = self._run()
        rec = run["recommendations"][0]
        rec["blocked"] = True
        with self.assertRaises(HTTPException) as raised:
            main.create_po_drafts_endpoint({
                "run_id": run["run_id"], "recommendation_ids": [rec["recommendation_id"]],
            })
        self.assertEqual(raised.exception.status_code, 409)

    def test_every_push_route_fails_closed(self):
        for endpoint in (main.push_po_draft_endpoint, main.push_po_draft_v2_endpoint):
            with self.assertRaises(HTTPException) as raised:
                endpoint("draft-id", {})
            self.assertEqual(raised.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
