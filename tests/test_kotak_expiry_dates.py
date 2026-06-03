"""Kotak get_expiry_dates filters expired series from scrip search."""

import datetime
import unittest
from unittest.mock import MagicMock, patch

from config import INDEX_CONFIGS
from brokers.kotak_client import KotakNeoClient


class TestKotakExpiryDates(unittest.TestCase):
    @patch("brokers.kotak_client.datetime")
    def test_filters_past_expiries(self, mock_dt):
        mock_dt.datetime.strptime.side_effect = datetime.datetime.strptime
        ist_now = datetime.datetime(2026, 6, 4, 10, 0, 0)
        mock_dt.datetime.now.return_value = ist_now

        client = MagicMock(spec=KotakNeoClient)
        client._api = MagicMock()
        client._api.search_scrip.return_value = [
            {"pExpiryDate": "03JUN2026"},
            {"pExpiryDate": "10JUN2026"},
            {"pExpiryDate": "17JUN2026"},
        ]

        with patch.object(KotakNeoClient, "_ensure", lambda self: None):
            out = KotakNeoClient.get_expiry_dates(client, INDEX_CONFIGS["NIFTY"])

        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].date(), datetime.date(2026, 6, 10))
        self.assertEqual(out[1].date(), datetime.date(2026, 6, 17))


if __name__ == "__main__":
    unittest.main()
