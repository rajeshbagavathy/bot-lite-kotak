"""Portfolio MTM from strategies + hedges."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mtm import calculate_portfolio_mtm_from_strategies  # noqa: E402


class TestPortfolioMtmFromStrategies(unittest.TestCase):
    def test_straddle_plus_hedge(self):
        strategies = [
            {
                "name": "S1",
                "status": "OPEN",
                "instrument_ids": [100, 200],
                "positions": [
                    {"instrument_id": 100, "quantity": -195, "entry_price": 77.6},
                    {"instrument_id": 200, "quantity": -195, "entry_price": 79.85},
                ],
            },
            {
                "name": "S2",
                "status": "OPEN",
                "instrument_ids": [100, 200],
                "positions": [
                    {"instrument_id": 100, "quantity": -195, "entry_price": 74.55},
                    {"instrument_id": 200, "quantity": -195, "entry_price": 81.05},
                ],
            },
        ]
        broker = [
            {
                "ExchangeInstrumentId": 300,
                "Quantity": 390,
                "KotakBuyAmount": 887.25,
                "KotakSellAmount": 0.0,
                "Multiplier": 1,
                "OpenBuyQuantity": 390,
                "OpenSellQuantity": 0,
                "SumOfTradedQuantityAndPriceBuy": 887.25,
                "SumOfTradedQuantityAndPriceSell": 0.0,
            }
        ]
        ltp = {100: 50.0, 200: 32.0, 300: 0.45}
        _, _, total = calculate_portfolio_mtm_from_strategies(strategies, broker, ltp)
        # CE shorts profit + PE shorts profit + small hedge loss ≈ not dominated by one leg
        ce_pnl = (77.6 - 50) * 195 + (74.55 - 50) * 195
        pe_pnl = (79.85 - 32) * 195 + (81.05 - 32) * 195
        hedge_pnl = (0.45 - 887.25 / 390) * 390
        self.assertAlmostEqual(total, ce_pnl + pe_pnl + hedge_pnl, places=0)


if __name__ == "__main__":
    unittest.main()
