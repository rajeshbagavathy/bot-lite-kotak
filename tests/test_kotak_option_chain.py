"""Kotak option chain cache — one search_scrip per expiry, not per strike."""

import unittest
from unittest.mock import MagicMock, patch

from config import INDEX_CONFIGS
from brokers.kotak_client import KotakNeoClient


class TestKotakOptionChainCache(unittest.TestCase):
    def _client_with_rows(self, rows):
        client = KotakNeoClient.__new__(KotakNeoClient)
        client._api = MagicMock()
        client._api.search_scrip.return_value = rows
        client._token_meta = {}
        client._option_chain_rows = {}
        client._option_id_index = {}
        client._index_ltp_ticks = {}
        client._sensex_spot_tok_cache = None
        return client

    @patch.object(KotakNeoClient, "_ensure", lambda self: None)
    def test_warm_option_chain_indexes_strikes(self):
        rows = [
            {"pSymbol": 100, "pTrdSymbol": "NIFTY09JUN23200CE", "pOptionType": "CE", "pStrikePrice": 23200},
            {"pSymbol": 101, "pTrdSymbol": "NIFTY09JUN23200PE", "pOptionType": "PE", "pStrikePrice": 23200},
        ]
        client = self._client_with_rows(rows)
        cfg = INDEX_CONFIGS["NIFTY"]
        n = client.warm_option_chain(cfg, "09JUN2026")
        self.assertEqual(n, 2)
        self.assertEqual(client._api.search_scrip.call_count, 1)
        ce = client.get_option_instrument_id(cfg, "09JUN2026", "CE", 23200)
        pe = client.get_option_instrument_id(cfg, "09JUN2026", "PE", 23200)
        self.assertEqual(ce, 100)
        self.assertEqual(pe, 101)
        self.assertEqual(client._api.search_scrip.call_count, 1)


if __name__ == "__main__":
    unittest.main()
