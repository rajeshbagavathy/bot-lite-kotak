"""Portfolio MTM from broker positions."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mtm import (  # noqa: E402
    calculate_mtm_kotak_booked_only,
    calculate_portfolio_mtm_from_broker,
)


class TestPortfolioMtmFromBroker(unittest.TestCase):
    def test_booked_only_after_close(self):
        positions = [
            {
                "ExchangeInstrumentId": 1,
                "Quantity": 0,
                "KotakBuyAmount": 5000.0,
                "KotakSellAmount": 4500.0,
                "Multiplier": 1,
            },
            {
                "ExchangeInstrumentId": 2,
                "Quantity": 0,
                "KotakBuyAmount": 100.0,
                "KotakSellAmount": 200.0,
                "Multiplier": 1,
            },
        ]
        r, u, t = calculate_mtm_kotak_booked_only(positions)
        self.assertEqual(u, 0.0)
        self.assertAlmostEqual(t, -400.0)  # -500 + 100
        self.assertAlmostEqual(r, -400.0)

        br, bu, bt, src = calculate_portfolio_mtm_from_broker(positions, {}, market_open=False)
        self.assertEqual(src, "broker_booked_closed")
        self.assertAlmostEqual(bt, -400.0)

    def test_open_short_uses_ltp_when_market_open(self):
        positions = [
            {
                "ExchangeInstrumentId": 42272,
                "Quantity": -195,
                "KotakBuyAmount": 0.0,
                "KotakSellAmount": 15132.0,
                "OpenSellQuantity": 195,
                "OpenBuyQuantity": 0,
                "SumOfTradedQuantityAndPriceSell": 15132.0,
                "SumOfTradedQuantityAndPriceBuy": 0.0,
                "Multiplier": 1,
            }
        ]
        _, _, total, src = calculate_portfolio_mtm_from_broker(positions, {42272: 60.0}, market_open=True)
        self.assertEqual(src, "broker_kotak_amounts")
        self.assertGreater(total, 3000.0)


if __name__ == "__main__":
    unittest.main()
