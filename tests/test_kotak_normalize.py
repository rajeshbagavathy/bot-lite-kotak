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
from mtm import calculate_mtm, calculate_mtm_kotak_amounts  # noqa: E402


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

    def test_fno_short_row_uses_sell_amount_not_zero_avg(self):
        """Kotak FNO rows often lack avgPrc; sellAmt/flSellQty must drive MTM."""
        rows = [
            {
                "trdSym": "NIFTY09JUN202623100CE",
                "tok": "42272",
                "prod": "MIS",
                "posFlg": "true",
                "flBuyQty": "0",
                "flSellQty": "195",
                "cfBuyQty": "0",
                "cfSellQty": "0",
                "buyAmt": "0.00",
                "sellAmt": "15132.00",
                "lotSz": "65",
                "multiplier": "1",
                "genNum": "1",
                "genDen": "1",
                "prcNum": "1",
                "prcDen": "1",
            }
        ]
        out = kotak_positions_to_normalized(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["Quantity"], -195)
        self.assertAlmostEqual(out[0]["AveragePrice"], 15132.0 / 195.0, places=2)
        self.assertAlmostEqual(out[0]["SumOfTradedQuantityAndPriceSell"], 15132.0)
        _, unrealized, total = calculate_mtm(out, {42272: 60.0})
        # Short 195 @ ~77.6, LTP 60 → profit ~3432
        self.assertGreater(total, 3000.0)
        self.assertGreater(unrealized, 3000.0)

    def test_fno_hedge_long_row(self):
        rows = [
            {
                "trdSym": "NIFTY09JUN202622900PE",
                "tok": "42265",
                "prod": "MIS",
                "posFlg": "true",
                "flBuyQty": "195",
                "flSellQty": "0",
                "buyAmt": "419.25",
                "sellAmt": "0.00",
                "lotSz": "65",
                "multiplier": "1",
            }
        ]
        out = kotak_positions_to_normalized(rows)
        self.assertEqual(out[0]["Quantity"], 195)
        _, _, total = calculate_mtm(out, {42265: 2.5})
        # Long hedge ~2.15 → 2.5 small profit
        self.assertGreater(total, 0.0)

    def test_kotak_amounts_formula_short(self):
        rows = kotak_positions_to_normalized(
            [
                {
                    "trdSym": "NIFTY09JUN202623100CE",
                    "tok": "42272",
                    "prod": "MIS",
                    "posFlg": "true",
                    "flSellQty": "195",
                    "flBuyQty": "0",
                    "sellAmt": "15132.00",
                    "buyAmt": "0.00",
                    "multiplier": "1",
                }
            ]
        )
        r, u, t = calculate_mtm_kotak_amounts(rows, {42272: 60.0})
        self.assertIsNotNone(r)
        assert r is not None and u is not None and t is not None
        self.assertAlmostEqual(t, 15132.0 - 195 * 60.0, places=1)
        self.assertGreater(t, 3000.0)


if __name__ == "__main__":
    unittest.main()
