"""Tests: startup cleanup removes prior-day spot_market_data rows."""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import db


class TestSpotMarketCleanup(unittest.TestCase):
    def test_cleanup_previous_day_data_removes_old_spot_bars(self):
        ist_today = "2026-04-09"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tf:
            path = tf.name
        try:
            with patch.object(db, "DB_PATH", path), patch.object(db, "get_ist_date", return_value=ist_today):
                db.init_db()
                conn = sqlite3.connect(path)
                conn.execute(
                    """
                    INSERT INTO spot_market_data
                    (index_name, bar_time, open, high, low, close, volume, is_calmzone)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("NIFTY", "2026-04-08 09:15:00", 1, 2, 1, 2, 0, 0),
                )
                conn.execute(
                    """
                    INSERT INTO spot_market_data
                    (index_name, bar_time, open, high, low, close, volume, is_calmzone)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("SENSEX", "2026-04-09 09:15:00", 1, 2, 1, 2, 0, 0),
                )
                conn.commit()
                conn.close()

                db.cleanup_previous_day_data()

                conn = sqlite3.connect(path)
                rows = conn.execute(
                    "SELECT index_name, bar_time FROM spot_market_data ORDER BY index_name"
                ).fetchall()
                conn.close()
                self.assertEqual(rows, [("SENSEX", "2026-04-09 09:15:00")])
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
