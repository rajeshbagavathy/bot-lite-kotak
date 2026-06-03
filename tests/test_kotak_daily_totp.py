import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from db import (
    get_ist_date,
    init_db,
    is_kotak_totp_satisfied_today,
    mark_kotak_totp_satisfied_today,
)


class TestKotakDailyTotp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self._tmp.close()
        self._db_patch = patch("db.DB_PATH", self._tmp.name)
        self._db_patch.start()
        init_db()

    def tearDown(self):
        self._db_patch.stop()
        os.unlink(self._tmp.name)

    def test_mark_and_check_today(self):
        self.assertFalse(is_kotak_totp_satisfied_today())
        mark_kotak_totp_satisfied_today()
        self.assertTrue(is_kotak_totp_satisfied_today())

    def test_previous_day_not_satisfied(self):
        conn = sqlite3.connect(self._tmp.name)
        conn.execute(
            "INSERT INTO kotak_daily_auth (ist_date, submitted_at) VALUES (?, ?)",
            ("2000-01-01", "2000-01-01 09:00:00"),
        )
        conn.commit()
        conn.close()
        self.assertFalse(is_kotak_totp_satisfied_today())
        today = get_ist_date()
        self.assertNotEqual(today, "2000-01-01")


if __name__ == "__main__":
    unittest.main()
