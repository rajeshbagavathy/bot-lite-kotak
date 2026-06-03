"""Unit tests for Kotak → XTS-shaped normalizers."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from brokers.mappers.kotak_normalize import (  # noqa: E402
    _instrument_token_from_kotak_row,
    kotak_order_to_normalized,
    kotak_positions_to_normalized,
    parse_kotak_place_order_n_ord_no,
)


class TestKotakOrderNormalize(unittest.TestCase):
    def test_sample_order_report_row(self):
        row = {
            "nOrdNo": "250122000612876",
            "ordSt": "open",
            "stat": "open",
            "trdSym": "IDEA-EQ",
            "tok": "14366",
            "trnsTp": "B",
            "prcTp": "L",
            "qty": 1,
            "fldQty": 0,
            "unFldSz": 1,
            "trgPrc": "0.00",
            "prc": "9.39",
            "vldt": "DAY",
            "prod": "NRML",
            "GuiOrdId": "MYTAG",
        }
        n = kotak_order_to_normalized(row)
        self.assertEqual(n["AppOrderID"], 250122000612876)
        self.assertEqual(n["OrderStatus"], "NEW")
        self.assertEqual(n["OrderSide"], "BUY")
        self.assertEqual(n["ExchangeInstrumentID"], 14366)
        self.assertEqual(n["OrderUniqueIdentifier"], "MYTAG")
        self.assertEqual(n["ProductType"], "NRML")


class TestKotakPlaceOrderParse(unittest.TestCase):
    def test_n_ord_no(self):
        self.assertEqual(
            parse_kotak_place_order_n_ord_no({"stat": "Ok", "nOrdNo": "123", "stCode": 200}),
            123,
        )
        self.assertIsNone(parse_kotak_place_order_n_ord_no({"Error": "x"}))


class TestKotakInstrumentToken(unittest.TestCase):
    def test_psymbol_when_tok_empty(self):
        row = {"tok": "", "pSymbol": "88123", "trdSym": "NIFTY25APR25000CE", "prod": "MIS"}
        self.assertEqual(_instrument_token_from_kotak_row(row), 88123)


class TestKotakPositionsNormalize(unittest.TestCase):
    def test_single_row_net(self):
        rows = [
            {
                "trdSym": "NIFTY24JAN24000CE",
                "tok": "12345",
                "prod": "MIS",
                "qty": -65,
                "avgPrc": "100.5",
                "multiplier": "1",
            }
        ]
        out = kotak_positions_to_normalized(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["Quantity"], -65)
        self.assertEqual(out[0]["ExchangeInstrumentId"], 12345)
        self.assertEqual(out[0]["OpenSellQuantity"], 65)
        self.assertEqual(out[0]["OpenBuyQuantity"], 0)
        self.assertEqual(out[0]["SumOfTradedQuantityAndPriceSell"], 65 * 100.5)
        self.assertEqual(out[0]["Multiplier"], 1)

    def test_long_position_mtm_fields(self):
        rows = [
            {
                "trdSym": "NIFTY24JAN24000PE",
                "tok": "999",
                "prod": "MIS",
                "qty": 130,
                "avgPrc": "50",
                "multiplier": "1",
            }
        ]
        out = kotak_positions_to_normalized(rows)
        self.assertEqual(out[0]["OpenBuyQuantity"], 130)
        self.assertEqual(out[0]["OpenSellQuantity"], 0)
        self.assertEqual(out[0]["SumOfTradedQuantityAndPriceBuy"], 130 * 50.0)


if __name__ == "__main__":
    unittest.main()
