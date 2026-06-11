"""Tests for EOD square-off helpers and bot integration."""
import unittest
from unittest.mock import MagicMock, patch

import bot
from trading.context import STRATEGY_STATE
from trading.eod import (
    collect_bot_tracked_instrument_ids,
    is_bot_sl_order_tag,
    is_cancellable_order_status,
    is_eod_halt,
    reset_eod_state,
)


class TestEodHelpers(unittest.TestCase):
    def setUp(self):
        self._orig = dict(STRATEGY_STATE)
        STRATEGY_STATE.clear()
        STRATEGY_STATE["N_T_1101"] = {
            "name": "N_T_1101",
            "instrument_ids": [111, 222],
            "positions": [{"instrument_id": 111, "exit_price": None}],
            "sl_tag_map": {"N_T_1101_SL_111": 111},
            "hedge_orders": [{"instrument_id": 333, "quantity": 65, "side": "PE"}],
        }

    def tearDown(self):
        STRATEGY_STATE.clear()
        STRATEGY_STATE.update(self._orig)
        reset_eod_state()

    def test_collect_bot_tracked_instrument_ids(self):
        ids = collect_bot_tracked_instrument_ids()
        self.assertEqual(ids, {111, 222, 333})

    def test_is_bot_sl_order_tag(self):
        self.assertTrue(is_bot_sl_order_tag("N_T_1101_SL_111"))
        self.assertFalse(is_bot_sl_order_tag("MANUAL_SL_111"))

    def test_is_cancellable_order_status(self):
        self.assertTrue(is_cancellable_order_status("NEW"))
        self.assertFalse(is_cancellable_order_status("FILLED"))


class TestEodSquareOffBot(unittest.TestCase):
    def setUp(self):
        self._orig = dict(STRATEGY_STATE)
        STRATEGY_STATE.clear()
        STRATEGY_STATE["N_T_1101"] = {
            "name": "N_T_1101",
            "status": "OPEN",
            "instrument_ids": [111, 222],
            "positions": [],
            "sl_orders": [{"app_order_id": 99, "tag": "N_T_1101_SL_111"}],
            "sl_tag_map": {},
            "db_id": 1,
        }
        reset_eod_state()

    def tearDown(self):
        STRATEGY_STATE.clear()
        STRATEGY_STATE.update(self._orig)
        reset_eod_state()

    def test_square_off_bot_positions_only(self):
        client = MagicMock()
        index_config = MagicMock()
        positions = [
            {"ExchangeInstrumentId": "111", "Quantity": "-65", "ProductType": "MIS"},
            {"ExchangeInstrumentId": "999", "Quantity": "-65", "ProductType": "MIS"},
        ]
        placed = bot._square_off_bot_positions(client, index_config, positions, "test")
        self.assertEqual(placed, 1)
        client.place_market_order.assert_called_once()

    def test_cancel_bot_open_sl_orders(self):
        client = MagicMock()
        order_book = [
            {"AppOrderID": 99, "OrderUniqueIdentifier": "N_T_1101_SL_111", "OrderStatus": "NEW"},
            {"AppOrderID": 100, "OrderUniqueIdentifier": "OTHER_SL_111", "OrderStatus": "NEW"},
        ]
        n = bot._cancel_bot_open_sl_orders(client, order_book=order_book)
        self.assertEqual(n, 2)
        self.assertEqual(client.cancel_order.call_count, 2)

    @patch("bot._update_eod_banner_state")
    @patch("bot.log_trade_closed")
    @patch("bot.update_strategy")
    def test_eod_squareoff_and_cleanup(self, mock_update, mock_log_closed, _banner):
        client = MagicMock()
        index_config = MagicMock()
        client.get_positions.return_value = [
            {"ExchangeInstrumentId": "111", "Quantity": "-65", "ProductType": "MIS"},
        ]
        client.get_order_book.return_value = [
            {"AppOrderID": 99, "OrderUniqueIdentifier": "N_T_1101_SL_111", "OrderStatus": "NEW"},
        ]
        self.assertFalse(is_eod_halt())
        bot._eod_squareoff_and_cleanup(client, index_config)
        self.assertTrue(is_eod_halt())
        client.place_market_order.assert_called()
        mock_log_closed.assert_called_once()
        mock_update.assert_called()
