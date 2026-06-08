"""
Unit tests for SL protection feature (journal + lifecycle + order helpers).

Run with coverage gate:
  coverage run -m pytest tests/test_sl_protection_unit.py tests/integration/test_sl_protection_e2e.py -q
  coverage report
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot  # noqa: F401 — enables compat.resolve patches
import trading.context as ctx
import trading.journal as journal_mod
from config import IndexConfig
from trading.compat import resolve
from trading.journal import Phase, init_journal, journal_path, read_tail, record
from trading.orders.book import get_filled_orders
from trading.orders.lifecycle import (
    SlProtectionResult,
    complete_entry_with_sl_protection,
    enforce_open_strategy_sl_invariant,
    flatten_exposure,
    wait_for_entry_fills,
)
from trading.orders.sl import (
    place_leg_sl_orders,
    rebuild_sl_links_from_order_book,
    verify_sl_orders_live,
)
from trading.state_bridge import set_spot, update_strategy


class TestJournal(unittest.TestCase):
    def setUp(self):
        journal_mod._journal_path = None
        os.environ.pop("TRADE_JOURNAL_PATH", None)

    def test_init_journal_explicit_path(self):
        path = init_journal("/tmp/test_journal.jsonl")
        self.assertEqual(path, os.path.abspath("/tmp/test_journal.jsonl"))
        self.assertEqual(os.environ["TRADE_JOURNAL_PATH"], path)

    def test_init_journal_from_env(self):
        with patch.dict(os.environ, {"TRADE_JOURNAL_PATH": "/tmp/from_env.jsonl"}, clear=False):
            journal_mod._journal_path = None
            path = init_journal()
            self.assertTrue(path.endswith("from_env.jsonl"))

    def test_init_journal_from_bot_log_path(self):
        with patch.dict(
            os.environ,
            {"BOT_LOG_PATH": "/var/log/bot.log", "TRADE_JOURNAL_PATH": ""},
            clear=False,
        ):
            journal_mod._journal_path = None
            os.environ.pop("TRADE_JOURNAL_PATH", None)
            path = init_journal()
            self.assertEqual(path, os.path.join("/var/log", "trade_journal.jsonl"))

    def test_init_journal_default_project_base(self):
        with patch.dict(os.environ, {}, clear=True):
            journal_mod._journal_path = None
            path = init_journal()
            self.assertTrue(path.endswith("trade_journal.jsonl"))
            self.assertTrue(os.path.isabs(path))

    def test_journal_path_lazy_init(self):
        journal_mod._journal_path = None
        os.environ.pop("TRADE_JOURNAL_PATH", None)
        p = journal_path()
        self.assertIn("trade_journal.jsonl", os.path.basename(p))

    def test_record_and_read_tail(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.remove(path)
        init_journal(path)
        record(Phase.ENTRY_SENT, "S1", "hello", foo=1)
        record("SL_VERIFY", "S1", "ok", severity="ERROR", detail="x")
        lines = [l for l in read_tail(10) if l.get("strategy") == "S1"]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["phase"], Phase.ENTRY_SENT.value)
        self.assertEqual(lines[1]["severity"], "ERROR")
        bad_path = path + ".bad"
        with open(bad_path, "w", encoding="utf-8") as fh:
            fh.write('{"phase":"X"}\n{bad json\n')
        journal_mod._journal_path = bad_path
        parsed = read_tail(10)
        self.assertEqual(len(parsed), 1)
        os.remove(path)
        os.remove(bad_path)

    def test_read_tail_missing_file(self):
        journal_mod._journal_path = "/nonexistent/path/journal.jsonl"
        self.assertEqual(read_tail(), [])

    def test_read_tail_filters_by_strategy(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"phase":"A","strategy":"S1","message":"m1","ts":"t1","severity":"INFO","details":{}}\n')
            fh.write('{"phase":"B","strategy":"S2","message":"m2","ts":"t2","severity":"INFO","details":{}}\n')
        journal_mod._journal_path = path
        filtered = read_tail(10, strategy="S1")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["strategy"], "S1")
        os.remove(path)

    @patch("builtins.open", side_effect=OSError("disk full"))
    def test_record_write_oserror(self, _mock_open):
        init_journal("/tmp/j.jsonl")
        record(Phase.PROTECTED, "S", "msg")  # should not raise

    def test_read_tail_oserror(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        journal_mod._journal_path = path
        with patch("os.path.isfile", return_value=True):
            with patch("builtins.open", side_effect=OSError("read fail")):
                self.assertEqual(read_tail(), [])
        os.remove(path)

    def test_read_tail_skips_blank_lines(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('\n{"phase":"OK"}\n\n')
        journal_mod._journal_path = path
        lines = read_tail(10)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["phase"], "OK")
        os.remove(path)

    def test_record_truncates_long_details(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.remove(path)
        init_journal(path)
        record(Phase.ENTRY_SENT, "S", "msg", payload="x" * 500)
        with open(path, encoding="utf-8") as fh:
            self.assertIn('"payload"', fh.read())
        os.remove(path)

    def test_journal_event_to_dict(self):
        from trading.journal import JournalEvent

        ev = JournalEvent(phase="P", strategy="S", message="m", details={"a": 1})
        self.assertIn("ts", ev.to_dict())


class TestCompatAndStateBridge(unittest.TestCase):
    def test_resolve_uses_bot_override(self):
        bot.__dict__["_custom_fn"] = lambda: "from_bot"
        try:
            self.assertEqual(resolve("_custom_fn", lambda: "fallback")(), "from_bot")
        finally:
            del bot.__dict__["_custom_fn"]

    def test_resolve_fallback_without_bot_attr(self):
        self.assertEqual(resolve("_missing_attr_xyz", 42), 42)

    @patch("bot.update_strategy")
    def test_state_bridge_update_strategy(self, mock_upd):
        update_strategy("S", status="ERROR")
        mock_upd.assert_called_once()

    @patch("bot.set_spot")
    def test_state_bridge_set_spot(self, mock_spot):
        set_spot(123.45)
        mock_spot.assert_called_once_with(123.45)


class TestGetFilledOrders(unittest.TestCase):
    def test_filters_by_id_status_and_price(self):
        book = [
            {"AppOrderID": 1, "OrderStatus": "Filled", "OrderAverageTradedPrice": 10.0},
            {"AppOrderID": 2, "OrderStatus": "Pending", "OrderAverageTradedPrice": 0.0},
            {"AppOrderID": 3, "OrderStatus": "PartiallyFilled", "OrderAverageTradedPrice": 12.0},
        ]
        out = get_filled_orders(book, [1, 2, 3])
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["AppOrderID"], 1)
        self.assertEqual(out[1]["AppOrderID"], 3)


class TestPlaceAndVerifySlOrders(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.client.interactive.TRANSACTION_TYPE_BUY = "BUY"
        self.index = IndexConfig(
            name="NIFTY",
            fno_symbol="NIFTY",
            lot_size=65,
            strike_diff=50,
            spot_exchange_segment=1,
            spot_instrument_id=26000,
            option_ltp_segment=2,
            option_exchange_segment="OPTIDX",
            order_exchange_segment="NSEFO",
            tick_size=0.05,
        )
        self.filled = [
            {
                "ExchangeInstrumentID": 111,
                "OrderAverageTradedPrice": 100.0,
                "OrderQuantity": 65,
                "OrderQuantityTraded": 65,
                "ProductType": "MIS",
                "TradingSymbol": "CE",
            },
            {
                "ExchangeInstrumentID": 222,
                "OrderAverageTradedPrice": 0,
                "OrderQuantity": 0,
                "ProductType": "MIS",
            },
        ]

    @patch("trading.orders.sl.log_order")
    def test_place_leg_sl_orders_success_and_skip_bad_qty(self, _log):
        self.client.place_sl_order.side_effect = [501, None]
        bad_qty = dict(self.filled[1])
        bad_qty["OrderQuantityTraded"] = "not-a-number"
        sls, tag_map = place_leg_sl_orders(
            self.client, self.index, self.filled[:1] + [bad_qty], 20.0, "S_TEST"
        )
        self.assertEqual(len(sls), 1)
        self.assertIn("S_TEST_SL_111", tag_map)

    @patch("trading.orders.sl.log_order")
    def test_place_leg_sl_skips_when_broker_returns_no_id(self, _log):
        self.client.place_sl_order.return_value = None
        sls, tag_map = place_leg_sl_orders(
            self.client, self.index, self.filled[:1], 20.0, "S_TEST"
        )
        self.assertEqual(sls, [])
        self.assertEqual(tag_map, {})

    def test_verify_sl_empty_and_missing_ids(self):
        ok, why = verify_sl_orders_live(self.client, "S", [])
        self.assertFalse(ok)
        self.assertEqual(why, "no_sl_orders_created")
        ok, why = verify_sl_orders_live(self.client, "S", [{"tag": "t"}])
        self.assertFalse(ok)
        self.assertEqual(why, "sl_order_ids_missing")

    @patch("time.sleep")
    def test_verify_sl_rejected(self, _sleep):
        self.client.get_order_book.return_value = [
            {"AppOrderID": 9, "OrderStatus": "Rejected"},
        ]
        ok, why = verify_sl_orders_live(
            self.client, "S", [{"app_order_id": 9}], max_wait_seconds=2, poll_interval=1
        )
        self.assertFalse(ok)
        self.assertIn("sl_bad_status", why)

    @patch("time.sleep")
    def test_verify_sl_success_after_poll(self, _sleep):
        self.client.get_order_book.side_effect = [
            [],
            [{"AppOrderID": 8, "OrderStatus": "New"}],
        ]
        ok, why = verify_sl_orders_live(
            self.client, "S", [{"app_order_id": 8}], max_wait_seconds=4, poll_interval=2
        )
        self.assertTrue(ok)

    @patch("time.sleep")
    def test_verify_sl_book_error_then_timeout(self, _sleep):
        self.client.get_order_book.side_effect = [Exception("net"), []]
        ok, why = verify_sl_orders_live(
            self.client, "S", [{"app_order_id": 7}], max_wait_seconds=2, poll_interval=1
        )
        self.assertFalse(ok)
        self.assertIn("sl_missing_in_order_book", why)

    @patch("time.sleep")
    def test_verify_sl_skips_malformed_book_rows(self, _sleep):
        self.client.get_order_book.return_value = [
            {"AppOrderID": None, "OrderStatus": "New"},
            {"AppOrderID": "bad-id", "OrderStatus": "New"},
            {"AppOrderID": 7, "OrderStatus": "New"},
        ]
        ok, why = verify_sl_orders_live(
            self.client, "S", [{"app_order_id": 7}], max_wait_seconds=2, poll_interval=1
        )
        self.assertTrue(ok)
        self.assertEqual(why, "sl_verified_in_order_book")

    @patch("trading.orders.sl.update_strategy")
    def test_rebuild_sl_links(self, mock_upd):
        strategy = {
            "name": "S_TEST",
            "positions": [{"instrument_id": 111, "exit_price": None}],
        }
        book = [
            {
                "OrderUniqueIdentifier": "S_TEST_SL_111",
                "AppOrderID": 900,
                "ExchangeInstrumentID": 111,
                "OrderStatus": "New",
            }
        ]
        self.assertTrue(rebuild_sl_links_from_order_book(strategy, book))
        mock_upd.assert_called_once()

    def test_rebuild_sl_links_false_cases(self):
        self.assertFalse(rebuild_sl_links_from_order_book({"name": "S"}, None))
        self.assertFalse(rebuild_sl_links_from_order_book({"name": ""}, [{"x": 1}]))
        self.assertFalse(
            rebuild_sl_links_from_order_book({"name": "S", "positions": []}, [{"x": 1}])
        )
        self.assertFalse(
            rebuild_sl_links_from_order_book(
                {"name": "S", "positions": [{"instrument_id": 1, "exit_price": None}]},
                [{"OrderUniqueIdentifier": "OTHER", "AppOrderID": 1}],
            )
        )

    @patch("trading.orders.sl.update_strategy")
    def test_rebuild_sl_links_parses_tag_and_closed_legs(self, mock_upd):
        strategy = {
            "name": "S_TEST",
            "positions": [
                {"instrument_id": 111, "exit_price": None},
                {"instrument_id": 222, "exit_price": 1.0},
            ],
        }
        book = [
            {"OrderUniqueIdentifier": "OTHER", "AppOrderID": 1},
            {"OrderUniqueIdentifier": "S_TEST_SL_111", "AppOrderID": None},
            {"OrderUniqueIdentifier": "S_TEST_SL_bad", "AppOrderID": "nope"},
            {
                "OrderUniqueIdentifier": "S_TEST_SL_222",
                "InstrumentId": "222",
                "AppOrderID": 901,
            },
            {
                "OrderUniqueIdentifier": "S_TEST_SL_333",
                "AppOrderID": 902,
            },
            {
                "OrderUniqueIdentifier": "S_TEST_SL_111",
                "AppOrderID": 900,
                "ExchangeInstrumentID": "bad",
                "InstrumentID": "also-bad",
            },
            {
                "OrderUniqueIdentifier": "S_TEST_SL_NOPE",
                "AppOrderID": 904,
            },
            {
                "OrderUniqueIdentifier": "S_TEST_SL_111",
                "AppOrderID": 903,
                "ExchangeInstrumentID": 111,
            },
        ]
        self.assertTrue(rebuild_sl_links_from_order_book(strategy, book))
        mock_upd.assert_called_once()
        sl_orders = mock_upd.call_args.kwargs["sl_orders"]
        self.assertEqual(len(sl_orders), 2)


class TestLifecycleWaitAndFlatten(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.index = MagicMock()
        journal_mod._journal_path = None
        init_journal(os.path.join(os.environ.get("TMPDIR", "/tmp"), "lc_journal.jsonl"))

    @patch("trading.orders.lifecycle.update_order_status")
    @patch("time.sleep")
    def test_wait_for_entry_fills_success(self, _sleep, mock_upd):
        self.client.get_order_book.return_value = []
        with patch(
            "trading.orders.lifecycle.resolve",
            side_effect=lambda name, fb: (
                lambda book, ids: [
                    {"AppOrderID": 1, "OrderAverageTradedPrice": 10.0},
                    {"OrderAverageTradedPrice": 11.0},
                ]
                if name == "_get_filled_orders"
                else fb
            ),
        ):
            filled, status = wait_for_entry_fills(self.client, "S", [1, 2], 2)
        self.assertEqual(status, "filled")
        self.assertEqual(len(filled), 2)
        mock_upd.assert_called_once()

    @patch("time.sleep")
    def test_wait_for_entry_fills_order_book_error(self, _sleep):
        self.client.get_order_book.side_effect = Exception("down")
        filled, status = wait_for_entry_fills(self.client, "S", [1], 1)
        self.assertEqual(status, "timeout")
        self.assertEqual(filled, [])

    @patch("trading.orders.lifecycle.close_positions_for_instruments")
    def test_flatten_exposure_success(self, mock_close):
        self.client.get_positions.return_value = [{"ExchangeInstrumentId": 1, "Quantity": -65}]
        self.assertTrue(flatten_exposure(self.client, self.index, "S", [1], "reason"))
        mock_close.assert_called_once()

    @patch("trading.orders.lifecycle.close_positions_for_instruments", side_effect=RuntimeError("fail"))
    def test_flatten_exposure_failure(self, _mock_close):
        self.client.get_positions.return_value = []
        self.assertFalse(flatten_exposure(self.client, self.index, "S", [1], "reason"))


class TestCompleteEntryWithSlProtection(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.index = MagicMock()
        journal_mod._journal_path = None
        init_journal(os.path.join(os.environ.get("TMPDIR", "/tmp"), "ce_journal.jsonl"))
        self.strategy = {"name": "S_E2E", "db_id": 1}
        self.placed = [
            {"app_order_id": 1001, "instrument_id": 12345},
            {"app_order_id": 1002, "instrument_id": 67890},
        ]
        self.filled = [
            {
                "AppOrderID": 1001,
                "ExchangeInstrumentID": 12345,
                "OrderQuantity": 65,
                "OrderAverageTradedPrice": 150.0,
                "TradingSymbol": "CE",
            },
            {
                "AppOrderID": 1002,
                "ExchangeInstrumentID": 67890,
                "OrderQuantity": 65,
                "OrderAverageTradedPrice": 145.0,
                "TradingSymbol": "PE",
            },
        ]

    @patch("trading.orders.lifecycle.log_position")
    @patch("trading.orders.lifecycle.update_strategy")
    @patch("trading.orders.lifecycle.wait_for_entry_fills")
    @patch("trading.orders.lifecycle.time.sleep")
    def test_happy_path_protected(self, _sleep, mock_wait, mock_upd, mock_log_pos):
        mock_wait.return_value = (self.filled, "filled")
        with patch(
            "trading.orders.lifecycle.resolve",
            side_effect=lambda name, fb: {
                "_place_leg_sl_orders": lambda *a, **k: (
                    [{"app_order_id": 2001, "tag": "S_E2E_SL_12345"}, {"app_order_id": 2002, "tag": "S_E2E_SL_67890"}],
                    {"S_E2E_SL_12345": 12345, "S_E2E_SL_67890": 67890},
                ),
                "_verify_sl_orders_live": lambda *a, **k: (True, "sl_verified_in_order_book"),
            }.get(name, fb),
        ):
            result = complete_entry_with_sl_protection(
                self.client, self.index, self.strategy, self.placed, 20.0, 30.0
            )
        self.assertTrue(result.ok)
        self.assertEqual(len(result.positions), 2)
        self.assertEqual(len(result.sl_orders), 2)
        mock_log_pos.assert_called()
        mock_upd.assert_not_called()

    @patch("trading.orders.lifecycle.update_strategy")
    @patch("trading.orders.lifecycle.flatten_exposure", return_value=True)
    @patch("trading.orders.lifecycle.wait_for_entry_fills")
    @patch("trading.orders.lifecycle.time.sleep")
    def test_fill_timeout_with_exposure_flattens(self, _sleep, mock_wait, mock_flat, mock_upd):
        mock_wait.return_value = ([], "timeout")
        self.client.get_positions.return_value = [
            {"ExchangeInstrumentId": 12345, "Quantity": -65},
        ]
        result = complete_entry_with_sl_protection(
            self.client, self.index, self.strategy, self.placed, 20.0, 30.0
        )
        self.assertFalse(result.ok)
        self.assertTrue(result.flattened)
        mock_flat.assert_called_once()
        mock_upd.assert_called_once()

    @patch("trading.orders.lifecycle.update_strategy")
    @patch("trading.orders.lifecycle.wait_for_entry_fills")
    @patch("trading.orders.lifecycle.time.sleep")
    def test_fill_timeout_no_exposure_aborts(self, _sleep, mock_wait, mock_upd):
        mock_wait.return_value = ([], "timeout")
        self.client.get_positions.return_value = []
        result = complete_entry_with_sl_protection(
            self.client, self.index, self.strategy, self.placed, 20.0, 30.0
        )
        self.assertFalse(result.ok)
        self.assertFalse(result.flattened)
        mock_upd.assert_called_once()

    @patch("trading.orders.lifecycle.update_strategy")
    @patch("trading.orders.lifecycle.wait_for_entry_fills")
    @patch("trading.orders.lifecycle.time.sleep")
    def test_fill_timeout_positions_fetch_error(self, _sleep, mock_wait, mock_upd):
        mock_wait.return_value = ([], "timeout")
        self.client.get_positions.side_effect = Exception("pos err")
        result = complete_entry_with_sl_protection(
            self.client, self.index, self.strategy, self.placed, 20.0, 30.0
        )
        self.assertFalse(result.ok)
        mock_upd.assert_called_once()

    @patch("trading.orders.lifecycle.update_strategy")
    @patch("trading.orders.lifecycle.flatten_exposure", return_value=True)
    @patch("trading.orders.lifecycle.wait_for_entry_fills")
    @patch("trading.orders.lifecycle.time.sleep")
    def test_sl_verify_missing_flattens(self, _sleep, mock_wait, mock_flat, mock_upd):
        mock_wait.return_value = (self.filled, "filled")
        with patch(
            "trading.orders.lifecycle.resolve",
            side_effect=lambda name, fb: {
                "_place_leg_sl_orders": lambda *a, **k: ([{"app_order_id": 1, "tag": "t"}], {"t": 12345}),
                "_verify_sl_orders_live": lambda *a, **k: (False, "sl_missing_in_order_book(count=1)"),
            }.get(name, fb),
        ):
            result = complete_entry_with_sl_protection(
                self.client, self.index, self.strategy, self.placed, 20.0, 30.0
            )
        self.assertFalse(result.ok)
        self.assertTrue(result.flattened)
        mock_flat.assert_called_once()

    @patch("trading.orders.lifecycle.update_strategy")
    @patch("trading.orders.lifecycle.flatten_exposure", return_value=True)
    @patch("trading.orders.lifecycle.wait_for_entry_fills")
    @patch("trading.orders.lifecycle.time.sleep")
    def test_sl_verify_rejected_phase(self, _sleep, mock_wait, _mock_flat, _mock_upd):
        mock_wait.return_value = (self.filled, "filled")
        with patch(
            "trading.orders.lifecycle.resolve",
            side_effect=lambda name, fb: {
                "_place_leg_sl_orders": lambda *a, **k: ([{"app_order_id": 1, "tag": "t"}], {"t": 12345}),
                "_verify_sl_orders_live": lambda *a, **k: (False, "sl_bad_status(Rejected)"),
            }.get(name, fb),
        ):
            result = complete_entry_with_sl_protection(
                self.client, self.index, self.strategy, self.placed, 20.0, 30.0
            )
        self.assertFalse(result.ok)
        path = journal_path()
        with open(path, encoding="utf-8") as fh:
            phases = [json.loads(line)["phase"] for line in fh if line.strip()]
        self.assertIn(Phase.SL_REJECTED.value, phases)

    @patch("trading.orders.lifecycle.log_position")
    @patch("trading.orders.lifecycle.wait_for_entry_fills")
    @patch("trading.orders.lifecycle.time.sleep")
    def test_skips_invalid_filled_row(self, _sleep, mock_wait, _log):
        bad_filled = [{"AppOrderID": 1, "OrderAverageTradedPrice": "bad"}]
        mock_wait.return_value = (bad_filled, "filled")
        with patch(
            "trading.orders.lifecycle.resolve",
            side_effect=lambda name, fb: {
                "_place_leg_sl_orders": lambda *a, **k: ([], {}),
                "_verify_sl_orders_live": lambda *a, **k: (True, "ok"),
            }.get(name, fb),
        ):
            result = complete_entry_with_sl_protection(
                self.client, self.index, {"name": "S", "db_id": 0}, self.placed, 20.0, 30.0
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.positions, [])


class TestEnforceOpenStrategySlInvariant(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.index = MagicMock()
        journal_mod._journal_path = None
        init_journal(os.path.join(os.environ.get("TMPDIR", "/tmp"), "inv_journal.jsonl"))

    def test_not_open_returns_false(self):
        s = {"status": "CLOSED", "name": "S"}
        self.assertFalse(
            enforce_open_strategy_sl_invariant(self.client, self.index, s, [], [])
        )

    def test_no_exposure_returns_false(self):
        s = {"status": "OPEN", "name": "S", "instrument_ids": [1]}
        self.assertFalse(
            enforce_open_strategy_sl_invariant(self.client, self.index, s, [], [])
        )

    @patch("trading.orders.lifecycle.flatten_exposure", return_value=True)
    @patch("trading.orders.lifecycle.update_strategy")
    def test_missing_sl_tracking_flattens(self, mock_upd, mock_flat):
        s = {"status": "OPEN", "name": "S", "instrument_ids": [12345], "sl_orders": []}
        positions = [{"ExchangeInstrumentId": 12345, "Quantity": -65}]
        self.assertTrue(
            enforce_open_strategy_sl_invariant(self.client, self.index, s, positions, [])
        )
        mock_flat.assert_called_once()
        mock_upd.assert_called_once()

    def test_order_book_none_returns_false(self):
        s = {
            "status": "OPEN",
            "name": "S",
            "instrument_ids": [12345],
            "sl_orders": [{"app_order_id": 1, "tag": "t"}],
        }
        positions = [{"ExchangeInstrumentId": 12345, "Quantity": -65}]
        self.assertFalse(
            enforce_open_strategy_sl_invariant(self.client, self.index, s, positions, None)
        )

    @patch("trading.orders.lifecycle.flatten_exposure", return_value=True)
    @patch("trading.orders.lifecycle.update_strategy")
    def test_bad_sl_in_order_book_flattens(self, mock_upd, mock_flat):
        s = {
            "status": "OPEN",
            "name": "S",
            "instrument_ids": [12345],
            "sl_orders": [{"app_order_id": 99, "tag": "t"}],
        }
        positions = [{"ExchangeInstrumentId": 12345, "Quantity": -65}]
        book = [{"AppOrderID": 99, "OrderStatus": "Cancelled"}]
        self.assertTrue(
            enforce_open_strategy_sl_invariant(self.client, self.index, s, positions, book)
        )
        mock_flat.assert_called_once()
        mock_upd.assert_called_once()

    def test_valid_sl_returns_false(self):
        s = {
            "status": "OPEN",
            "name": "S",
            "instrument_ids": [12345],
            "sl_orders": [{"app_order_id": 99, "tag": "t"}],
        }
        positions = [{"ExchangeInstrumentId": 12345, "Quantity": -65}]
        book = [{"AppOrderID": 99, "OrderStatus": "New"}]
        self.assertFalse(
            enforce_open_strategy_sl_invariant(self.client, self.index, s, positions, book)
        )

    def test_order_book_malformed_rows_ignored(self):
        s = {
            "status": "OPEN",
            "name": "S",
            "instrument_ids": [12345],
            "sl_orders": [{"app_order_id": 99, "tag": "t"}],
        }
        positions = [{"ExchangeInstrumentId": 12345, "Quantity": -65}]
        book = [{"AppOrderID": None}, {"AppOrderID": "bad"}, {"AppOrderID": 99, "OrderStatus": "New"}]
        self.assertFalse(
            enforce_open_strategy_sl_invariant(self.client, self.index, s, positions, book)
        )

    @patch("trading.orders.lifecycle.flatten_exposure", return_value=True)
    @patch("trading.orders.lifecycle.update_strategy")
    def test_invalid_sl_order_id_flattens(self, _mock_upd, mock_flat):
        s = {
            "status": "OPEN",
            "name": "S",
            "instrument_ids": [12345],
            "sl_orders": [{"app_order_id": "bad", "tag": "t"}],
        }
        positions = [{"ExchangeInstrumentId": 12345, "Quantity": -65}]
        book = []
        self.assertTrue(
            enforce_open_strategy_sl_invariant(self.client, self.index, s, positions, book)
        )
        mock_flat.assert_called_once()

    @patch("trading.orders.lifecycle.flatten_exposure", return_value=True)
    @patch("trading.orders.lifecycle.update_strategy")
    def test_sl_missing_from_book_flattens(self, _mock_upd, mock_flat):
        s = {
            "status": "OPEN",
            "name": "S",
            "instrument_ids": [12345],
            "sl_orders": [{"app_order_id": 50, "tag": "t"}],
        }
        positions = [{"ExchangeInstrumentId": 12345, "Quantity": -65}]
        self.assertTrue(
            enforce_open_strategy_sl_invariant(self.client, self.index, s, positions, [])
        )
        mock_flat.assert_called_once()


class TestContextModule(unittest.TestCase):
    def test_context_defaults(self):
        self.assertIsInstance(ctx.STRATEGY_STATE, dict)
        self.assertEqual(ctx._MAIN_LOOP_LAST_TICK, 0.0)


class TestSlProtectionResultDataclass(unittest.TestCase):
    def test_defaults(self):
        r = SlProtectionResult(ok=True)
        self.assertEqual(r.sl_orders, [])
        self.assertIsNone(r.error_message)


if __name__ == "__main__":
    unittest.main()
