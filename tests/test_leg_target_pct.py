"""Leg target % by expiry day."""
import unittest
from unittest.mock import patch

from trading.utils import leg_target_pct


class TestLegTargetPct(unittest.TestCase):
    @patch("trading.utils.is_expiry_day", return_value=True)
    def test_expiry_day_uses_80(self, _mock):
        self.assertEqual(leg_target_pct("03JUN2026"), 80.0)

    @patch("trading.utils.is_expiry_day", return_value=False)
    def test_non_expiry_day_uses_50(self, _mock):
        self.assertEqual(leg_target_pct("05JUN2026"), 50.0)

    @patch("trading.utils.is_expiry_day", return_value=False)
    def test_missing_expiry_defaults_non_expiry(self, _mock):
        self.assertEqual(leg_target_pct(None), 50.0)
