"""Tests for EOD square-off helpers and bot integration."""
import datetime
import unittest
from unittest.mock import MagicMock, patch

import bot
import pytz
from trading.context import STRATEGY_STATE
from trading.eod import (
    collect_bot_tracked_instrument_ids,
    count_remaining_bot_exposure,
    is_at_or_past_eod_verify_until,
    is_bot_sl_order_tag,
    is_cancellable_order_status,
    is_eod_halt,
    is_within_eod_retry_window,
    record_eod_close_placed,
    reset_eod_state,
    should_place_eod_close,
)

_IST = pytz.timezone("Asia/Kolkata")


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

    def test_eod_retry_window_1510_to_1519(self):
        d = datetime.date(2026, 6, 3)
        t_start = _IST.localize(datetime.datetime.combine(d, datetime.time(15, 10)))
        t_mid = _IST.localize(datetime.datetime.combine(d, datetime.time(15, 15)))
        t_stop = _IST.localize(datetime.datetime.combine(d, datetime.time(15, 19)))
        t_after = _IST.localize(datetime.datetime.combine(d, datetime.time(15, 20)))
        self.assertTrue(is_within_eod_retry_window(t_start))
        self.assertTrue(is_within_eod_retry_window(t_mid))
        self.assertFalse(is_within_eod_retry_window(t_stop))
        self.assertFalse(is_within_eod_retry_window(t_after))
        self.assertTrue(is_at_or_past_eod_verify_until(t_stop))
        self.assertFalse(is_at_or_past_eod_verify_until(t_mid))

    def test_count_remaining_all_positions_when_state_empty(self):
        STRATEGY_STATE.clear()
        positions = [
            {"ExchangeInstrumentId": 111, "Quantity": -65},
            {"ExchangeInstrumentId": 999, "Quantity": -30},
        ]
        book = [
            {"OrderUniqueIdentifier": "X_H_0946_SL_111", "OrderStatus": "NEW"},
        ]
        open_pos, open_sl = count_remaining_bot_exposure(positions, book)
        self.assertEqual(open_pos, 2)
        self.assertEqual(open_sl, 1)

    def test_count_remaining_eod_verify_counts_all_broker_positions(self):
        STRATEGY_STATE["N_T_1101"] = {
            "name": "N_T_1101",
            "instrument_ids": [111],
            "positions": [],
            "sl_tag_map": {},
            "hedge_orders": [],
        }
        positions = [
            {"ExchangeInstrumentId": 111, "Quantity": -65},
            {"ExchangeInstrumentId": 999, "Quantity": -30},
        ]
        open_pos, open_sl = count_remaining_bot_exposure(positions, [], eod_verify=True)
        self.assertEqual(open_pos, 2)

    def test_should_place_eod_close_skips_duplicate_signed_qty(self):
        reset_eod_state()
        self.assertTrue(should_place_eod_close(111, 780))
        record_eod_close_placed(111, 780)
        self.assertFalse(should_place_eod_close(111, 780))
        self.assertTrue(should_place_eod_close(111, 390))
        self.assertTrue(should_place_eod_close(111, -780))


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

    def test_square_off_all_when_no_tracked_ids(self):
        STRATEGY_STATE.clear()
        client = MagicMock()
        index_config = MagicMock()
        positions = [
            {"ExchangeInstrumentId": "111", "Quantity": "-65", "ProductType": "MIS"},
            {"ExchangeInstrumentId": "999", "Quantity": "-65", "ProductType": "MIS"},
        ]
        placed = bot._square_off_bot_positions(client, index_config, positions, "test")
        self.assertEqual(placed, 2)
        self.assertEqual(client.place_market_order.call_count, 2)

    def test_eod_mode_skips_duplicate_close_same_qty(self):
        reset_eod_state()
        client = MagicMock()
        index_config = MagicMock()
        positions = [
            {"ExchangeInstrumentId": "111", "Quantity": "780", "ProductType": "MIS"},
        ]
        placed1 = bot._square_off_bot_positions(
            client, index_config, positions, "test", eod_mode=True
        )
        placed2 = bot._square_off_bot_positions(
            client, index_config, positions, "test", eod_mode=True
        )
        self.assertEqual(placed1, 1)
        self.assertEqual(placed2, 0)
        self.assertEqual(client.place_market_order.call_count, 1)

    def test_cancel_bot_open_sl_orders(self):
        client = MagicMock()
        order_book = [
            {"AppOrderID": 99, "OrderUniqueIdentifier": "N_T_1101_SL_111", "OrderStatus": "NEW"},
            {"AppOrderID": 100, "OrderUniqueIdentifier": "OTHER_SL_111", "OrderStatus": "NEW"},
        ]
        n = bot._cancel_bot_open_sl_orders(client, order_book=order_book)
        self.assertEqual(n, 2)
        self.assertEqual(client.cancel_order.call_count, 2)

    @patch("bot._count_remaining_bot_exposure", return_value=(0, 0))
    @patch("bot.is_within_eod_retry_window", return_value=True)
    @patch("bot._update_eod_banner_state")
    @patch("bot.log_trade_closed")
    @patch("bot.update_strategy")
    def test_eod_squareoff_and_cleanup(self, mock_update, mock_log_closed, _banner, _window, _count):
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
