"""Tests for dashboard bot log survivor filter."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import unittest

from bot_log_filter import bot_log_line_is_survivor_event


class TestBotLogSurvivorFilter(unittest.TestCase):
    def test_survivor_tightened_line(self):
        line = (
            "2026-03-23 10:30:00,123 - WARNING - [N_M_0931] Survivor leg SL tightened to cost: "
            "instrument=123 limit=100.00 trigger=99.50 (entry=100.00)"
        )
        self.assertTrue(bot_log_line_is_survivor_event(line))

    def test_modify_failure_line(self):
        line = "2026-03-23 10:30:01 - ERROR - Failed to modify survivor SL to cost for N_M_0931: boom"
        self.assertTrue(bot_log_line_is_survivor_event(line))

    def test_order_book_warning(self):
        line = "Survivor SL order not in order book for N_M_0931 app_order_id=99"
        self.assertTrue(bot_log_line_is_survivor_event(line))

    def test_routine_info_excluded(self):
        line = "2026-03-23 10:26:45,422 - INFO - Database initialized at trades.db"
        self.assertFalse(bot_log_line_is_survivor_event(line))


if __name__ == "__main__":
    unittest.main()
