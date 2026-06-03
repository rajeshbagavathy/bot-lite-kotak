"""Kotak limits() → available margin parsing."""

import unittest

from brokers.kotak_client import KotakNeoClient


class TestKotakLimitsMarginParse(unittest.TestCase):
    def test_flat_net(self):
        payload = {"stat": "Ok", "stCode": 200, "Net": "1234567.5"}
        self.assertEqual(KotakNeoClient._parse_available_margin_from_limits(payload), 1234568)

    def test_data_dict_net(self):
        payload = {"stat": "Ok", "data": {"net": "500000"}}
        self.assertEqual(KotakNeoClient._parse_available_margin_from_limits(payload), 500000)

    def test_data_list_segments(self):
        payload = {
            "stat": "Ok",
            "data": [
                {"seg": "FO", "Net": "100"},
                {"segment": "ALL", "Net": "2500000"},
            ],
        }
        # Prefers ALL segment row over FO when both are present.
        self.assertEqual(KotakNeoClient._parse_available_margin_from_limits(payload), 2500000)

    def test_data_list_picks_max_when_no_all(self):
        payload = {"stat": "Ok", "data": [{"seg": "FO", "Net": "100"}, {"seg": "CASH", "Net": "900"}]}
        self.assertEqual(KotakNeoClient._parse_available_margin_from_limits(payload), 900)

    def test_error_returns_none(self):
        self.assertIsNone(KotakNeoClient._parse_available_margin_from_limits({"Error": "session"}))


if __name__ == "__main__":
    unittest.main()
