"""Tests: OHLC upserts do not wipe calm metrics once computed (static per minute)."""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import db


class TestSpotMetricsPreserve(unittest.TestCase):
    def test_ohlc_refresh_keeps_range_and_calm_flag(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tf:
            path = tf.name
        try:
            with patch.object(db, "DB_PATH", path):
                db.init_db()
                db.upsert_spot_bar(
                    "NIFTY",
                    "2026-04-10 10:55:00",
                    100.0,
                    101.0,
                    99.0,
                    100.5,
                    1000.0,
                    19.3,
                    0.5,
                    0.07,
                    True,
                    bar_unix=1_700_000_000,
                )
                db.upsert_spot_ohlc_only(
                    "NIFTY",
                    "2026-04-10 10:55:00",
                    100.1,
                    101.2,
                    99.1,
                    100.6,
                    1001.0,
                    bar_unix=1_700_000_000,
                )
                conn = sqlite3.connect(path)
                r = conn.execute(
                    "SELECT range_5m, net_body, body_range_ratio, is_calmzone FROM spot_market_data "
                    "WHERE index_name='NIFTY' AND bar_time='2026-04-10 10:55:00'"
                ).fetchone()
                conn.close()
                self.assertIsNotNone(r)
                self.assertAlmostEqual(r[0], 19.3)
                self.assertAlmostEqual(r[1], 0.5)
                self.assertAlmostEqual(r[2], 0.07)
                self.assertEqual(r[3], 1)
        finally:
            os.unlink(path)

    def test_locked_calm_row_is_immutable_for_recompute_upsert(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tf:
            path = tf.name
        try:
            with patch.object(db, "DB_PATH", path):
                db.init_db()
                db.upsert_spot_bar(
                    "NIFTY",
                    "2026-04-10 10:56:00",
                    100.0,
                    101.0,
                    99.0,
                    100.5,
                    1000.0,
                    19.3,
                    0.5,
                    0.07,
                    True,
                    bar_unix=1_700_000_060,
                )
                # A second full upsert for the same minute should be ignored once calm_locked=1.
                db.upsert_spot_bar(
                    "NIFTY",
                    "2026-04-10 10:56:00",
                    200.0,
                    205.0,
                    198.0,
                    204.0,
                    2000.0,
                    99.9,
                    9.9,
                    0.99,
                    False,
                    bar_unix=1_700_000_120,
                )
                conn = sqlite3.connect(path)
                r = conn.execute(
                    "SELECT open, high, low, close, range_5m, net_body, body_range_ratio, is_calmzone, calm_locked, bar_unix "
                    "FROM spot_market_data WHERE index_name='NIFTY' AND bar_time='2026-04-10 10:56:00'"
                ).fetchone()
                conn.close()
                self.assertIsNotNone(r)
                self.assertAlmostEqual(r[0], 100.0)
                self.assertAlmostEqual(r[1], 101.0)
                self.assertAlmostEqual(r[2], 99.0)
                self.assertAlmostEqual(r[3], 100.5)
                self.assertAlmostEqual(r[4], 19.3)
                self.assertAlmostEqual(r[5], 0.5)
                self.assertAlmostEqual(r[6], 0.07)
                self.assertEqual(r[7], 1)
                self.assertEqual(r[8], 1)
                self.assertEqual(r[9], 1_700_000_060)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
