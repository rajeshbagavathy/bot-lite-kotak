import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import db


class TestSpotOffsetNormalize(unittest.TestCase):
    def test_merge_same_bar_unix_and_rekey_bar_time(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tf:
            path = tf.name
        try:
            with patch.object(db, "DB_PATH", path), patch.object(db, "get_ist_date", return_value="2026-04-10"):
                db.init_db()
                conn = sqlite3.connect(path)
                conn.execute(
                    """
                    INSERT INTO spot_market_data
                    (index_name, bar_time, open, high, low, close, volume, range_5m, net_body, body_range_ratio, is_calmzone, updated_at, bar_unix, calm_locked)
                    VALUES ('NIFTY','2026-04-10 18:00:00',1,2,1,2,0,10,1,0.1,1,'2026-04-10 12:00:00',1775824259,1)
                    """
                )
                conn.execute(
                    """
                    INSERT INTO spot_market_data
                    (index_name, bar_time, open, high, low, close, volume, range_5m, net_body, body_range_ratio, is_calmzone, updated_at, bar_unix, calm_locked)
                    VALUES ('NIFTY','2026-04-10 12:30:00',1,2,1,2,0,NULL,NULL,NULL,0,'2026-04-10 12:00:00',1775824259,0)
                    """
                )
                conn.commit()
                conn.close()

                affected = db.normalize_spot_rows_for_offset("NIFTY", -19800, today_only=True)
                self.assertGreaterEqual(affected, 1)

                conn = sqlite3.connect(path)
                rows = conn.execute(
                    "SELECT bar_time, calm_locked, range_5m, bar_unix FROM spot_market_data WHERE index_name='NIFTY' ORDER BY bar_time"
                ).fetchall()
                conn.close()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0][0], "2026-04-10 12:30:00")
                self.assertEqual(rows[0][1], 1)
                self.assertIsNotNone(rows[0][2])
                self.assertEqual(rows[0][3], 1775824259)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()

