"""The nightly scheduler must fire at most one scrape attempt per local day —
a failed scheduled run counts as that day's attempt (the `due` window spans the
rest of the day, so retrying on failure means a full paid run every 60s)."""
import unittest
from unittest.mock import MagicMock, patch

from app.services.price_intelligence import repository


class SchedulerGuardTests(unittest.TestCase):
    def _guard_sql(self, rows):
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows", return_value=rows) as mock_rows:
            blocked = repository.has_scheduler_blocking_run_on(
                "2026-07-22", "America/Vancouver")
        return blocked, mock_rows.call_args.args[0]

    def test_counts_failed_scheduled_attempts_as_blocking(self):
        blocked, sql = self._guard_sql([{"n": 1}])
        self.assertTrue(blocked)
        # Any success/partial run blocks, and so does any scheduled attempt
        # regardless of outcome — including status='failed'.
        self.assertIn("status IN ('success', 'partial')", sql)
        self.assertIn("OR trigger = 'scheduled'", sql)

    def test_not_blocked_when_no_runs_today(self):
        blocked, _ = self._guard_sql([{"n": 0}])
        self.assertFalse(blocked)


if __name__ == "__main__":
    unittest.main()
