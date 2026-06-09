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
            {
                "pSymbol": 100,
                "pTrdSymbol": "NIFTY09JUN202623200CE",
                "pOptionType": "CE",
                "dStrikePrice;": 2320000,
            },
            {
                "pSymbol": 101,
                "pTrdSymbol": "NIFTY09JUN202623200PE",
                "pOptionType": "PE",
                "dStrikePrice;": 2320000,
            },
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

    @patch.object(KotakNeoClient, "_ensure", lambda self: None)
    def test_chain_ltp_map_from_scrip_rows(self):
        rows = [
            {"pSymbol": 100, "pTrdSymbol": "NIFTY09JUN23200CE", "pOptionType": "CE", "pStrikePrice": 23200, "lLtp": 5.5},
            {"pSymbol": 101, "pTrdSymbol": "NIFTY09JUN22000PE", "pOptionType": "PE", "pStrikePrice": 22000, "lLtp": 3.2},
        ]
        client = self._client_with_rows(rows)
        cfg = INDEX_CONFIGS["NIFTY"]
        client.warm_option_chain(cfg, "09JUN2026")
        ltps = client.chain_ltp_map(cfg, "09JUN2026")
        self.assertEqual(ltps[("CE", 23200)], 5.5)
        self.assertEqual(ltps[("PE", 22000)], 3.2)

    def test_parse_kotak_strike_from_dStrikePrice_semicolon(self):
        ot, strike = KotakNeoClient._parse_scrip_strike_and_type(
            {
                "pTrdSymbol": "NIFTY09JUN202623100PE",
                "pOptionType": "PE",
                "dStrikePrice;": 2310000,
            }
        )
        self.assertEqual(ot, "PE")
        self.assertEqual(strike, 23100)

    def test_parse_strike_from_symbol_without_swallowing_expiry(self):
        ot, strike = KotakNeoClient._parse_scrip_strike_and_type(
            {"pTrdSymbol": "NIFTY09JUN202623200CE", "pOptionType": "CE"}
        )
        self.assertEqual(ot, "CE")
        self.assertEqual(strike, 23200)

    @patch.object(KotakNeoClient, "_ensure", lambda self: None)
    def test_reindex_option_chain_from_cached_rows(self):
        rows = [
            {"pSymbol": 100, "pTrdSymbol": "NIFTY09JUN23200CE", "pOptionType": "CE", "pStrikePrice": 23200},
        ]
        client = self._client_with_rows(rows)
        cfg = INDEX_CONFIGS["NIFTY"]
        client._option_chain_rows[("NIFTY", "09JUN2026")] = rows
        self.assertEqual(client.reindex_option_chain(cfg, "09JUN2026"), 1)
        self.assertEqual(client.get_option_instrument_id(cfg, "09JUN2026", "CE", 23200, allow_search=False), 100)


if __name__ == "__main__":
    unittest.main()
