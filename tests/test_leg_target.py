"""Leg target: modify existing SL to marketable LIMIT (no separate exit order)."""
import unittest
from unittest.mock import MagicMock, patch

import bot
from trading.context import STRATEGY_STATE
from trading.journal import Phase, init_journal


class TestLegTargetModifySl(unittest.TestCase):
    def setUp(self):
        self._orig = dict(STRATEGY_STATE)
        STRATEGY_STATE.clear()
        init_journal("/tmp/test_leg_target_journal.jsonl")
        self.client = MagicMock()
        self.index_config = MagicMock()
        self.index_config.tick_size = 0.05
        self.index_config.option_ltp_segment = 12
        self.client.interactive.ORDER_TYPE_LIMIT = "LIMIT"
        self.client.interactive.TRANSACTION_TYPE_BUY = "BUY"

    def tearDown(self):
        STRATEGY_STATE.clear()
        STRATEGY_STATE.update(self._orig)

    def _strategy(self):
        return {
            "name": "X_H_0946",
            "status": "OPEN",
            "target_triggered_instruments": [],
            "positions": [
                {"instrument_id": 1132629, "entry_price": 311.45, "exit_price": None, "quantity": -60},
                {"instrument_id": 1132353, "entry_price": 300.0, "exit_price": 359.7, "quantity": -60},
            ],
            "sl_orders": [
                {"app_order_id": 128975, "tag": "X_H_0946_SL_1132353"},
                {"app_order_id": 128979, "tag": "X_H_0946_SL_1132629"},
            ],
            "sl_tag_map": {
                "X_H_0946_SL_1132353": 1132353,
                "X_H_0946_SL_1132629": 1132629,
            },
        }

    @patch("bot.update_strategy")
    @patch("bot.journal_record")
    def test_modifies_existing_sl_not_separate_exit(self, mock_journal, mock_update):
        STRATEGY_STATE["X_H_0946"] = self._strategy()
        strategy = STRATEGY_STATE["X_H_0946"]
        strategy["leg_target_pct"] = 60.0
        # 60%+ profit on CE: entry 311.45, ltp 100 -> profit ~68%
        ltp_map = {1132629: 100.0}
        self.client.get_order_book.return_value = [
            {
                "AppOrderID": 128979,
                "OrderUniqueIdentifier": "X_H_0946_SL_1132629",
                "OrderStatus": "NEW",
                "OrderQuantity": 60,
                "OrderSide": "BUY",
                "ProductType": "MIS",
                "TimeInForce": "DAY",
                "OrderPrice": 311.45,
            }
        ]
        self.client.modify_order.return_value = {"stat": "Ok", "stCode": 200}

        bot._check_leg_target_and_close(self.client, self.index_config, strategy, ltp_map)

        self.client.place_market_order.assert_not_called()
        self.client.modify_order.assert_called_once()
        kwargs = self.client.modify_order.call_args[1]
        self.assertEqual(kwargs["app_order_id"], 128979)
        self.assertEqual(kwargs["order_type"], "LIMIT")
        self.assertEqual(kwargs["stop_price"], 0)
        self.assertGreater(kwargs["limit_price"], 100.0)
        mock_update.assert_called_once()
        success_calls = [
            c
            for c in mock_journal.call_args_list
            if c[0][0] == Phase.LEG_TARGET_HIT and "awaiting SL fill" in c[0][2]
        ]
        self.assertEqual(len(success_calls), 1)

    @patch("bot.update_strategy")
    @patch("bot.journal_record")
    def test_does_not_mark_triggered_on_broker_reject(self, mock_journal, mock_update):
        STRATEGY_STATE["X_H_0946"] = self._strategy()
        strategy = STRATEGY_STATE["X_H_0946"]
        strategy["leg_target_pct"] = 60.0
        ltp_map = {1132629: 100.0}
        self.client.get_order_book.return_value = [
            {
                "AppOrderID": 128979,
                "OrderUniqueIdentifier": "X_H_0946_SL_1132629",
                "OrderStatus": "NEW",
                "OrderQuantity": 60,
                "OrderSide": "BUY",
                "ProductType": "MIS",
                "OrderPrice": 311.45,
            }
        ]
        self.client.modify_order.return_value = {"Message": "order not found"}

        bot._check_leg_target_and_close(self.client, self.index_config, strategy, ltp_map)

        mock_update.assert_not_called()
        err_calls = [c for c in mock_journal.call_args_list if c[1].get("severity") == "ERROR"]
        self.assertEqual(len(err_calls), 1)
