"""Full coverage tests for the trading package (100% gate with .coveragerc)."""
from __future__ import annotations

import datetime
import os
import sys
import threading
import unittest
from dataclasses import field
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot  # noqa: F401
import trading.context as ctx
import trading.journal as journal_mod
from config import IndexConfig
from trading.context import STRATEGY_STATE
from trading.journal import Phase, init_journal
from trading.orders.book import get_filled_orders, order_book_status_is_filled
from trading.orders.close import (
    _place_close_order,
    cancel_order_logged,
    close_positions_for_instruments,
    positions_exposure_for_instruments,
)
from trading.orders.lifecycle import SlProtectionResult
from trading.strategy.executor import _exec_locks, _exec_locks_guard, _strategy_exec_lock, execute_strategy
from trading.strategy.gatekeeper import (
    calm_gatekeeper_context_blurb,
    gatekeeper_window_start_iso,
    normalize_strategy_time_hhmmss,
    process_waiting_for_calm,
    should_execute_now,
    spot_row_is_calm,
    strategy_entry_window,
    strategy_slot_ist_datetime,
)
from trading.strategy.margin import ensure_margin_or_skip_strategy
from trading.strategy.strikes import find_hedge_by_target_premium, find_strike_by_premium
from trading.utils import (
    bot_tracked_hedge_buy_qty_by_side,
    bot_tracked_open_short_qty_by_side,
    compute_effective_lots_from_margin,
    compute_incremental_hedge_quantities,
    get_atm_strike,
    is_expiry_day,
    pick_index_and_expiry,
    round_to_tick,
)


def _release_all_strategy_exec_locks() -> None:
    with _exec_locks_guard:
        for lock in _exec_locks.values():
            while lock.locked():
                lock.release()
        _exec_locks.clear()


def _index_cfg(name="NIFTY", strike_diff=None):
    sd = strike_diff if strike_diff is not None else (100 if name == "SENSEX" else 50)
    return IndexConfig(
        name=name,
        fno_symbol=name,
        lot_size=65 if name == "NIFTY" else 20,
        strike_diff=sd,
        spot_exchange_segment=1,
        spot_instrument_id=26000,
        option_ltp_segment=2,
        option_exchange_segment="OPTIDX",
        order_exchange_segment="NSEFO",
        tick_size=0.05,
    )


class TestBookAndClose(unittest.TestCase):
    def setUp(self):
        journal_mod._journal_path = None
        init_journal(os.path.join(os.environ.get("TMPDIR", "/tmp"), "cov_close.jsonl"))

    def test_order_book_status_is_filled_variants(self):
        self.assertTrue(order_book_status_is_filled("Filled"))
        self.assertFalse(order_book_status_is_filled("New"))

    def test_positions_exposure_bad_rows(self):
        exp = positions_exposure_for_instruments([{"ExchangeInstrumentId": "x", "Quantity": 1}], [1])
        self.assertEqual(exp, {})

    def test_close_positions_and_place_close(self):
        client = MagicMock()
        client.interactive.TRANSACTION_TYPE_BUY = "BUY"
        client.interactive.TRANSACTION_TYPE_SELL = "SELL"
        client.interactive.PRODUCT_MIS = "MIS"
        cfg = _index_cfg()
        close_positions_for_instruments(
            client, cfg, [{"ExchangeInstrumentId": 9, "Quantity": -5, "ProductType": "MIS"}], [9],
            strategy_name="S", flow="unit",
        )
        client.place_market_order.assert_called_once()
        _place_close_order(client, cfg, {"ExchangeInstrumentId": 9, "Quantity": -5, "ProductType": "MIS"}, "X")
        _place_close_order(client, cfg, {"bad": "row"}, "X")

    def test_cancel_order_logged(self):
        client = MagicMock()
        cancel_order_logged(client, "S", 1, "t", flow="f")
        client.cancel_order.side_effect = RuntimeError("x")
        cancel_order_logged(client, "S", 2, "t2", flow="f")


class TestUtilsCoverage(unittest.TestCase):
    def test_round_to_tick(self):
        self.assertAlmostEqual(round_to_tick(100.01, 0.05), 100.05, places=2)

    @patch("trading.utils.get_today_strategies")
    @patch("trading.utils.INDEX_CONFIGS")
    def test_pick_index_errors(self, mock_cfgs, mock_today):
        client = MagicMock()
        mock_today.return_value = []
        with self.assertRaises(RuntimeError):
            pick_index_and_expiry(client)
        mock_today.return_value = [MagicMock()]
        mock_cfgs.values.return_value = [MagicMock(name="NIFTY")]
        client.get_expiry_dates.return_value = []
        with self.assertRaises(RuntimeError):
            pick_index_and_expiry(client)

    @patch("trading.utils.set_spot")
    def test_get_atm_strike(self, mock_spot):
        client = MagicMock()
        client.get_spot_ltp.return_value = None
        self.assertIsNone(get_atm_strike(client, _index_cfg()))
        client.get_spot_ltp.return_value = 22123.0
        self.assertEqual(get_atm_strike(client, _index_cfg()), 22100)

    def test_compute_effective_lots_force_one(self):
        with patch("trading.utils.MIN_MARGIN_TO_TRADE", 100000):
            eff, msg = compute_effective_lots_from_margin(10, 150000, 150000, 0, 200000)
            self.assertEqual(eff, 1)
            self.assertIn("forcing minimum 1 lot", msg or "")

    def test_bot_tracked_qty_branches(self):
        STRATEGY_STATE.clear()
        STRATEGY_STATE["S"] = {
            "positions": [
                {"symbol": "NIFTY24MAR22000CE", "quantity": -1, "exit_price": None},
                {"symbol": "NIFTY24MAR22000PE", "quantity": -2, "exit_price": 1},
                {"symbol": "NIFTY24MAR22100CE", "quantity": 3, "exit_price": None},
            ],
            "hedge_side_qty": {"PE": "bad", "CE": 3},
        }
        ce, pe = bot_tracked_open_short_qty_by_side()
        self.assertEqual(ce, 1)
        self.assertEqual(pe, 0)
        pe_h, ce_h = bot_tracked_hedge_buy_qty_by_side()
        self.assertEqual(ce_h, 3)
        STRATEGY_STATE["S2"] = {"hedge_orders": [{"side": "PE", "quantity": 5}, {"side": "XX", "quantity": 1}]}
        pe_h2, _ = bot_tracked_hedge_buy_qty_by_side()
        self.assertGreaterEqual(pe_h2, 5)

    def test_incremental_hedge_only_uncovered_qty(self):
        STRATEGY_STATE.clear()
        STRATEGY_STATE["S1"] = {
            "hedge_orders": [
                {"side": "PE", "quantity": 195},
                {"side": "CE", "quantity": 195},
            ],
            "positions": [
                {"symbol": "NIFTY CE", "quantity": -195, "exit_price": None},
                {"symbol": "NIFTY PE", "quantity": -195, "exit_price": None},
            ],
        }
        pe, ce, _ = compute_incremental_hedge_quantities(195)
        self.assertEqual(pe, 195)
        self.assertEqual(ce, 195)

    def test_incremental_hedge_skips_when_existing_covers_new_sells(self):
        STRATEGY_STATE.clear()
        STRATEGY_STATE["S1"] = {
            "hedge_orders": [
                {"side": "PE", "quantity": 390},
                {"side": "CE", "quantity": 390},
            ],
        }
        pe, ce = bot_tracked_hedge_buy_qty_by_side()
        self.assertEqual(pe, 390)
        pe_need, ce_need, _ = compute_incremental_hedge_quantities(195)
        self.assertEqual(pe_need, 0)
        self.assertEqual(ce_need, 0)

    def test_hedge_orders_preferred_over_portfolio_side_qty_totals(self):
        """Avoid double-counting when hedge_side_qty stored portfolio totals per strategy."""
        STRATEGY_STATE.clear()
        STRATEGY_STATE["S1"] = {
            "hedge_side_qty": {"PE": 390, "CE": 390},
            "hedge_orders": [{"side": "PE", "quantity": 195}, {"side": "CE", "quantity": 195}],
        }
        STRATEGY_STATE["S2"] = {
            "hedge_side_qty": {"PE": 390, "CE": 390},
            "hedge_orders": [{"side": "PE", "quantity": 195}, {"side": "CE", "quantity": 195}],
        }
        pe, ce = bot_tracked_hedge_buy_qty_by_side()
        self.assertEqual(pe, 390)
        self.assertEqual(ce, 390)


class TestStrikesCoverage(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.cfg = _index_cfg()

    def test_find_strike_no_candidates(self):
        self.client.get_option_instrument_id.return_value = None
        self.assertIsNone(find_strike_by_premium(self.client, self.cfg, "12FEB2026", "CE", 22000, 150, 100, 200, max_steps=1))

    def test_find_strike_ltp_filters(self):
        self.client.get_option_instrument_id.return_value = 103
        self.client.get_ltp_map.return_value = {103: 148.0}
        out = find_strike_by_premium(self.client, self.cfg, "12FEB2026", "CE", 22000, 150, 100, 200, max_steps=1)
        self.assertEqual(out, (22000, 103))

    def test_find_hedge_uses_token_meta_hint(self):
        client = MagicMock()
        client._token_meta = {77: {"ltp_hint": 5.0, "trdSym": "NIFTYPE"}}
        client.get_option_instrument_id.return_value = 77
        out = find_hedge_by_target_premium(client, self.cfg, "12FEB2026", "PE", 22000, 5, 3, 10, max_steps=1)
        self.assertEqual(out, {"strike": 21950, "instrument_id": 77, "ltp": 5.0})
        client.get_option_instrument_id.assert_called_with(
            self.cfg, "12FEB2026", "PE", 21950, allow_search=False
        )

    def test_find_hedge_no_match(self):
        self.client.get_option_instrument_id.return_value = 99
        self.client._token_meta = {99: {"ltp_hint": 100.0}}
        self.assertIsNone(
            find_hedge_by_target_premium(self.client, self.cfg, "12FEB2026", "CE", 22000, 5, 1, 3, max_steps=1)
        )

    def test_find_hedge_batch_quote_when_no_chain_ltp(self):
        client = MagicMock()
        client.get_option_instrument_id.return_value = 88
        client._token_meta = {}
        client.get_ltp_map.return_value = {88: 5.0}
        out = find_hedge_by_target_premium(client, self.cfg, "12FEB2026", "PE", 22000, 5, 3, 10, max_steps=1)
        self.assertEqual(out, {"strike": 21950, "instrument_id": 88, "ltp": 5.0})
        client.get_ltp_map.assert_called_once()

    def test_find_hedge_invalid_instrument_id(self):
        self.client.get_option_instrument_id.return_value = "bad"
        self.assertIsNone(find_hedge_by_target_premium(self.client, self.cfg, "12FEB2026", "PE", 22000, 5, 1, 10, max_steps=1))


class TestMarginCoverage(unittest.TestCase):
    def setUp(self):
        journal_mod._journal_path = None
        init_journal(os.path.join(os.environ.get("TMPDIR", "/tmp"), "cov_margin.jsonl"))
        STRATEGY_STATE.clear()
        self.client = MagicMock()
        self.client.interactive.TRANSACTION_TYPE_BUY = "BUY"
        self.client.interactive.PRODUCT_MIS = "MIS"
        self.cfg = _index_cfg()

    @patch("bot.HEDGE_ON_EVERY_STRATEGY", False)
    @patch("trading.strategy.margin.update_strategy")
    @patch("trading.strategy.margin.update_portfolio_margin")
    @patch("trading.strategy.margin.is_expiry_day", return_value=False)
    def test_margin_ok_no_resize(self, *_):
        strat = {"name": "S", "lots": 2}
        self.client.get_available_margin.return_value = 5_000_000
        self.assertTrue(ensure_margin_or_skip_strategy(self.client, self.cfg, "18Mar2026", strat, 22000))

    @patch("bot.HEDGE_ON_EVERY_STRATEGY", False)
    @patch("trading.strategy.margin.update_strategy")
    @patch("trading.strategy.margin.update_portfolio_margin")
    @patch("trading.strategy.margin.is_expiry_day", return_value=False)
    def test_margin_resize(self, mock_upd, *_):
        strat = {"name": "S", "lots": 10}
        self.client.get_available_margin.return_value = 1_400_000
        self.assertTrue(ensure_margin_or_skip_strategy(self.client, self.cfg, "18Mar2026", strat, 22000))
        self.assertLess(strat["lots"], 10)

    @patch("bot.HEDGE_ON_EVERY_STRATEGY", False)
    @patch("trading.strategy.margin.update_strategy")
    @patch("trading.strategy.margin.update_portfolio_margin")
    @patch("trading.strategy.margin.is_expiry_day", return_value=False)
    def test_margin_fail(self, *_):
        strat = {"name": "S", "lots": 50}
        self.client.get_available_margin.return_value = 10_000
        with patch("trading.utils.MIN_MARGIN_TO_TRADE", 999_999_999):
            self.assertFalse(ensure_margin_or_skip_strategy(self.client, self.cfg, "18Mar2026", strat, 22000))

    @patch("bot.HEDGE_ON_EVERY_STRATEGY", True)
    @patch("trading.strategy.margin.update_strategy")
    @patch("trading.strategy.margin.update_portfolio_margin")
    @patch("trading.strategy.margin.time.sleep")
    @patch("trading.strategy.margin.is_expiry_day", return_value=True)
    @patch("trading.strategy.margin.find_hedge_by_target_premium")
    def test_margin_hedge_success(self, mock_find, *_):
        mock_find.side_effect = [{"strike": 1, "instrument_id": 11}, {"strike": 2, "instrument_id": 22}]
        self.client.get_available_margin.side_effect = [5_000_000, 5_000_000]
        self.client.get_ltp_map.return_value = {11: 2.0, 22: 2.0}
        self.client.place_market_order.side_effect = [101, 102]
        strat = {"name": "S", "lots": 2}
        self.assertTrue(ensure_margin_or_skip_strategy(self.client, self.cfg, "17Mar2026", strat, 22000))

    @patch("bot.HEDGE_ON_EVERY_STRATEGY", True)
    @patch("trading.strategy.margin.update_strategy")
    @patch("trading.strategy.margin.update_portfolio_margin")
    @patch("trading.strategy.margin.is_expiry_day", return_value=True)
    @patch("bot._find_hedge_by_target_premium", return_value=None)
    def test_margin_hedge_not_found(self, *_):
        self.client.get_available_margin.return_value = 1_000_000
        strat = {"name": "S", "lots": 10}
        self.assertFalse(ensure_margin_or_skip_strategy(self.client, self.cfg, "17Mar2026", strat, 22000))

    @patch("bot.HEDGE_ON_EVERY_STRATEGY", True)
    @patch("trading.strategy.margin.update_strategy")
    @patch("trading.strategy.margin.update_portfolio_margin")
    @patch("trading.strategy.margin.is_expiry_day", return_value=True)
    @patch("trading.strategy.margin.find_hedge_by_target_premium")
    def test_margin_hedge_place_fail(self, mock_find, *_):
        mock_find.side_effect = [{"strike": 1, "instrument_id": 11}, {"strike": 2, "instrument_id": 22}]
        self.client.get_available_margin.return_value = 1_000_000
        self.client.get_ltp_map.return_value = {11: 2.0, 22: 2.0}
        self.client.place_market_order.side_effect = [101, None]
        strat = {"name": "S", "lots": 10}
        self.assertFalse(ensure_margin_or_skip_strategy(self.client, self.cfg, "17Mar2026", strat, 22000))


class TestGatekeeperCoverage(unittest.TestCase):
    @patch("bot.USE_CALM_ZONE_GATEKEEPER", True)
    @patch("bot.CALM_ZONE_GATEKEEPER_MODE", "bogus")
    @patch("bot.fetch_last_two_spot_bar_rows")
    def test_invalid_mode_fallback(self, mock_two):
        mock_two.return_value = [{"is_calmzone": 1}]
        ok, reason, _ = should_execute_now("S", "NIFTY")
        self.assertTrue(ok)

    @patch("bot.USE_CALM_ZONE_GATEKEEPER", True)
    @patch("bot.CALM_ZONE_GATEKEEPER_MODE", "recent_calm")
    @patch("bot.fetch_recent_calm_spot_row")
    @patch("bot.fetch_latest_spot_bar_row")
    def test_recent_calm_and_volatile(self, mock_latest, mock_recent):
        mock_recent.return_value = None
        mock_latest.return_value = {"is_calmzone": 0}
        ok, reason, _ = should_execute_now("S", "NIFTY")
        self.assertFalse(ok)
        self.assertEqual(reason, "volatile")

    @patch("bot.USE_CALM_ZONE_GATEKEEPER", True)
    @patch("bot.CALM_ZONE_GATEKEEPER_MODE", "latest_bar")
    @patch("bot.fetch_latest_spot_bar_row", return_value=None)
    def test_latest_no_data(self, *_):
        ok, reason, row = should_execute_now("S", "NIFTY")
        self.assertFalse(ok)
        self.assertIsNone(row)

    def test_spot_row_is_calm_fallback(self):
        self.assertTrue(spot_row_is_calm({"is_calmzone": 1}, "NIFTY"))
        self.assertFalse(spot_row_is_calm({"range_5m": 999, "body_range_ratio": 0.9}, "NIFTY"))

    def test_window_helpers(self):
        strat = {"time": "09:21:00"}
        self.assertTrue(gatekeeper_window_start_iso(strat).startswith("202"))
        slot, end = strategy_entry_window(strat, now=datetime.datetime(2026, 4, 8, 9, 0, 0))
        self.assertEqual(slot.hour, 9)

    @patch("trading.strategy.gatekeeper.mark_strategy_skipped_volatility_db")
    @patch("trading.strategy.gatekeeper.get_ist_now")
    @patch("bot.should_execute_now", return_value=(False, "volatile", {}))
    @patch("bot.update_strategy")
    def test_process_waiting_timeout(self, _upd, _gate, mock_now, _mark_db):
        mock_now.return_value = datetime.datetime(2026, 4, 8, 10, 30, 0)
        STRATEGY_STATE.clear()
        STRATEGY_STATE["S"] = {
            "name": "S",
            "status": "WAITING_FOR_CALM",
            "db_id": 1,
            "gatekeeper_started_at": "2026-04-08T09:21:00",
            "next_gatekeeper_check_at": 0,
        }
        process_waiting_for_calm(MagicMock(), MagicMock(name="NIFTY"), "10APR2026", lambda *a, **k: None)

    @patch("trading.strategy.gatekeeper.get_ist_now")
    def test_process_waiting_poll_skip(self, mock_now):
        mock_now.return_value = datetime.datetime(2026, 4, 8, 9, 25, 0)
        STRATEGY_STATE.clear()
        STRATEGY_STATE["S"] = {
            "name": "S",
            "status": "WAITING_FOR_CALM",
            "gatekeeper_started_at": "2026-04-08T09:21:00",
            "next_gatekeeper_check_at": mock_now.return_value.timestamp() + 999,
        }
        process_waiting_for_calm(MagicMock(), MagicMock(), "10APR2026", lambda *a, **k: None)


class TestExecutorCoverage(unittest.TestCase):
    def setUp(self):
        journal_mod._journal_path = None
        init_journal(os.path.join(os.environ.get("TMPDIR", "/tmp"), "cov_exec.jsonl"))
        STRATEGY_STATE.clear()
        _release_all_strategy_exec_locks()
        self.client = MagicMock()
        self.client.interactive.TRANSACTION_TYPE_SELL = "SELL"
        self.client.interactive.PRODUCT_MIS = "MIS"
        self.cfg = _index_cfg()

    def test_execute_skips_bad_status_and_time(self):
        execute_strategy(self.client, self.cfg, "12FEB2026", {"name": "S", "status": "OPEN", "time": "09:00:00"})
        with patch("trading.strategy.executor.get_ist_now") as mock_now:
            mock_now.return_value.strftime.return_value = "08:00:00"
            execute_strategy(
                self.client, self.cfg, "12FEB2026",
                {"name": "S", "status": "PENDING", "time": "09:00:00"},
                force=False,
            )

    def test_execute_lock_busy(self):
        _strategy_exec_lock("S").acquire()
        try:
            execute_strategy(
                self.client, self.cfg, "12FEB2026",
                {"name": "S", "status": "PENDING", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20},
                force=True,
            )
        finally:
            _strategy_exec_lock("S").release()

    def test_execute_locks_are_per_strategy(self):
        lock_a = _strategy_exec_lock("A")
        lock_b = _strategy_exec_lock("B")
        self.assertTrue(lock_a.acquire(blocking=False))
        self.assertTrue(lock_b.acquire(blocking=False))
        lock_a.release()
        lock_b.release()

    @patch("bot.should_execute_now", return_value=(True, "calm", {}))
    @patch("bot._get_atm_strike", return_value=None)
    @patch("bot.update_strategy")
    def test_execute_no_atm(self, *_):
        execute_strategy(
            self.client, self.cfg, "12FEB2026",
            {"name": "S", "status": "PENDING", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20},
            force=True,
        )

    @patch("bot.should_execute_now", return_value=(True, "calm", {}))
    @patch("bot._get_atm_strike", return_value=22000)
    @patch("trading.strategy.executor.get_trading_flag_or", return_value=True)
    @patch("trading.strategy.executor.find_strike_by_premium", return_value=None)
    @patch("bot.update_strategy")
    def test_execute_premium_strike_retry(self, *_):
        execute_strategy(
            self.client, self.cfg, "12FEB2026",
            {"name": "S", "status": "PENDING", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20},
            force=True,
        )

    @patch("bot.should_execute_now", return_value=(True, "calm", {}))
    @patch("bot._get_atm_strike", return_value=22000)
    @patch("trading.strategy.executor.is_expiry_day", return_value=True)
    @patch("trading.strategy.executor.ITM_STRIKES_NIFTY", 1)
    @patch("bot._ensure_margin_or_skip_strategy", return_value=True)
    @patch("trading.strategy.executor.complete_entry_with_sl_protection")
    @patch("bot.update_strategy")
    @patch("trading.strategy.executor.log_strategy_execution", return_value=7)
    @patch("trading.strategy.executor.log_order")
    @patch("time.time", return_value=1)
    @patch("time.sleep")
    def test_execute_expiry_itm_path(self, _sleep, _time, _log, _log_strat, _upd, mock_prot, *_):
        self.client.get_option_instrument_id.side_effect = [11, 22]
        self.client.get_ltp_map.return_value = {11: 1.0, 22: 1.0}
        self.client.place_market_order.side_effect = [1001, 1002]
        mock_prot.return_value = SlProtectionResult(ok=True, sl_orders=[], positions=[], sl_tag_map={})
        execute_strategy(
            self.client, self.cfg, "17Mar2026",
            {"name": "S", "status": "PENDING", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20},
            force=True,
        )

    @patch("bot.should_execute_now", return_value=(False, "volatile", {"bar_time": "t"}))
    @patch("bot._get_atm_strike", return_value=22000)
    @patch("trading.strategy.executor.is_expiry_day", return_value=False)
    @patch("trading.strategy.executor.USE_PREMIUM_BASED_STRIKE", False)
    @patch("bot._ensure_margin_or_skip_strategy", return_value=True)
    @patch("trading.strategy.executor.complete_entry_with_sl_protection")
    @patch("bot.update_strategy")
    @patch("trading.strategy.executor.log_strategy_execution", return_value=7)
    @patch("trading.strategy.executor.log_order")
    @patch("time.time", return_value=1)
    @patch("time.sleep")
    def test_execute_gatekeeper_force_skips_calm_recheck(self, _sleep, _time, _log, _log_strat, _upd, mock_prot, *_):
        """After gatekeeper CALM_PASSED, entry must not re-fail on a volatile re-read."""
        self.client.get_option_instrument_id.side_effect = [11, 22]
        self.client.get_ltp_map.return_value = {11: 1.0, 22: 1.0}
        self.client.place_market_order.side_effect = [1001, 1002]
        mock_prot.return_value = SlProtectionResult(ok=True, sl_orders=[], positions=[], sl_tag_map={})
        strat = {"name": "S", "status": "WAITING_FOR_CALM", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20}
        execute_strategy(self.client, self.cfg, "12FEB2026", strat, force=True)
        self.assertEqual(self.client.place_market_order.call_count, 2)

    @patch("bot.should_execute_now", return_value=(True, "calm", {}))
    @patch("bot._get_atm_strike", return_value=22000)
    @patch("trading.strategy.executor.is_expiry_day", return_value=False)
    @patch("trading.strategy.executor.USE_PREMIUM_BASED_STRIKE", False)
    @patch("bot._ensure_margin_or_skip_strategy", return_value=True)
    @patch("trading.strategy.executor.complete_entry_with_sl_protection")
    @patch("bot.update_strategy")
    @patch("trading.strategy.executor.log_strategy_execution", return_value=7)
    @patch("trading.strategy.executor.log_order")
    @patch("time.time", return_value=1)
    @patch("time.sleep")
    def test_execute_happy_path(self, _sleep, _time, _log, _log_strat, _upd, mock_prot, *_):
        self.client.get_option_instrument_id.side_effect = [11, 22]
        self.client.get_ltp_map.return_value = {11: 1.0, 22: 1.0}
        self.client.place_market_order.side_effect = [1001, 1002]
        mock_prot.return_value = SlProtectionResult(
            ok=True, sl_orders=[{"app_order_id": 9}], positions=[{"instrument_id": 11}], sl_tag_map={"t": 11},
        )
        strat = {"name": "S", "status": "WAITING_FOR_CALM", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20}
        execute_strategy(self.client, self.cfg, "12FEB2026", strat, force=True)
        self.assertEqual(self.client.place_market_order.call_count, 2)

    @patch("bot.should_execute_now", return_value=(True, "calm", {}))
    @patch("bot._get_atm_strike", return_value=22000)
    @patch("trading.strategy.executor.is_expiry_day", return_value=False)
    @patch("trading.strategy.executor.USE_PREMIUM_BASED_STRIKE", False)
    @patch("bot._ensure_margin_or_skip_strategy", return_value=False)
    @patch("bot.update_strategy")
    def test_execute_margin_fail(self, *_):
        self.client.get_option_instrument_id.side_effect = [11, 22]
        execute_strategy(
            self.client, self.cfg, "12FEB2026",
            {"name": "S", "status": "PENDING", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20},
            force=True,
        )

    @patch("bot.should_execute_now", return_value=(True, "calm", {}))
    @patch("bot._get_atm_strike", return_value=22000)
    @patch("trading.strategy.executor.is_expiry_day", return_value=False)
    @patch("trading.strategy.executor.USE_PREMIUM_BASED_STRIKE", False)
    @patch("bot._ensure_margin_or_skip_strategy", return_value=True)
    @patch("bot.update_strategy")
    @patch("time.time", return_value=1)
    def test_execute_entry_fail_rollback(self, *_):
        self.client.get_option_instrument_id.side_effect = [11, 22]
        self.client.get_ltp_map.return_value = {11: 1.0, 22: 1.0}
        self.client.place_market_order.side_effect = [1001, None]
        self.client.get_positions.return_value = []
        execute_strategy(
            self.client, self.cfg, "12FEB2026",
            {"name": "S", "status": "PENDING", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20},
            force=True,
        )


class TestContextModule(unittest.TestCase):
    def test_context_defaults(self):
        self.assertIsInstance(ctx.STRATEGY_STATE, dict)
        self.assertEqual(ctx._MAIN_LOOP_LAST_TICK, 0.0)


class TestRemainingCoverage(unittest.TestCase):
    """Branch coverage for modules not yet at 100%."""

    def setUp(self):
        journal_mod._journal_path = None
        init_journal(os.path.join(os.environ.get("TMPDIR", "/tmp"), "cov_rem.jsonl"))
        STRATEGY_STATE.clear()
        _release_all_strategy_exec_locks()

    @patch("bot.USE_CALM_ZONE_GATEKEEPER", True)
    @patch("bot.CALM_ZONE_GATEKEEPER_MODE", "current_or_prior_calm")
    @patch("bot.fetch_last_two_spot_bar_rows")
    def test_gatekeeper_current_and_prior_paths(self, mock_two):
        mock_two.return_value = [{"range_5m": 40, "body_range_ratio": 0.1, "bar_time": "t1"}]
        ok, reason, _ = should_execute_now("S", "NIFTY")
        self.assertEqual(reason, "calm_current")
        mock_two.return_value = [
            {"range_5m": 999, "body_range_ratio": 0.9, "bar_time": "t1"},
            {"range_5m": 40, "body_range_ratio": 0.1, "bar_time": "t0"},
        ]
        ok, reason, _ = should_execute_now("S", "NIFTY")
        self.assertEqual(reason, "calm_prior")
        mock_two.return_value = [
            {"range_5m": 999, "body_range_ratio": 0.9, "bar_time": "t1"},
            {"range_5m": 999, "body_range_ratio": 0.9, "bar_time": "t0"},
        ]
        ok, reason, row = should_execute_now("S", "NIFTY")
        self.assertFalse(ok)
        self.assertIn("prior_bar_time", row)

    @patch("bot.USE_CALM_ZONE_GATEKEEPER", True)
    @patch("bot.CALM_ZONE_GATEKEEPER_MODE", "latest_bar")
    @patch("bot.fetch_latest_spot_bar_row")
    def test_gatekeeper_latest_volatile(self, mock_latest):
        mock_latest.return_value = {"range_5m": 999, "body_range_ratio": 0.9}
        ok, reason, _ = should_execute_now("S", "NIFTY")
        self.assertFalse(ok)
        self.assertEqual(reason, "volatile")

    @patch("trading.strategy.gatekeeper.get_ist_now")
    @patch("bot.should_execute_now", return_value=(False, "volatile", {"bar_time": "t"}))
    @patch("bot.update_strategy")
    def test_process_waiting_sets_next_poll(self, _upd, _gate, mock_now):
        mock_now.return_value = datetime.datetime(2026, 4, 8, 9, 25, 0)
        STRATEGY_STATE["S"] = {
            "name": "S",
            "status": "WAITING_FOR_CALM",
            "gatekeeper_started_at": "2026-04-08T09:21:00",
            "next_gatekeeper_check_at": 0,
        }
        process_waiting_for_calm(MagicMock(), MagicMock(), "10APR2026", lambda *a, **k: None)

    @patch("trading.strategy.gatekeeper.get_ist_now")
    @patch("bot.should_execute_now", return_value=(True, "calm", {}))
    @patch("bot.update_strategy")
    def test_process_waiting_no_started_at(self, _upd, _gate, mock_now):
        mock_now.return_value = datetime.datetime(2026, 4, 8, 9, 25, 0)
        STRATEGY_STATE["S"] = {
            "name": "S",
            "status": "WAITING_FOR_CALM",
            "next_gatekeeper_check_at": 0,
        }
        process_waiting_for_calm(MagicMock(), MagicMock(), "10APR2026", lambda *a, **k: None)

    @patch("trading.strategy.gatekeeper.get_ist_now")
    @patch("bot.should_execute_now", return_value=(True, "calm", {}))
    @patch("bot.update_strategy")
    def test_process_waiting_bad_started_at(self, _upd, _gate, mock_now):
        mock_now.return_value = datetime.datetime(2026, 4, 8, 9, 25, 0)
        STRATEGY_STATE["S"] = {
            "name": "S",
            "status": "WAITING_FOR_CALM",
            "gatekeeper_started_at": "not-a-date",
            "next_gatekeeper_check_at": 0,
        }
        process_waiting_for_calm(MagicMock(), MagicMock(), "10APR2026", lambda *a, **k: None)

    def test_close_positions_skip_bad_iid(self):
        client = MagicMock()
        close_positions_for_instruments(client, _index_cfg(), [{"ExchangeInstrumentId": "bad", "Quantity": 1}], [1])

    @patch("bot.HEDGE_ON_EVERY_STRATEGY", True)
    @patch("trading.strategy.margin.update_strategy")
    @patch("trading.strategy.margin.update_portfolio_margin")
    @patch("trading.strategy.margin.is_expiry_day", return_value=True)
    @patch("trading.strategy.margin.find_hedge_by_target_premium")
    def test_margin_hedge_cancel_exception(self, mock_find, *_):
        mock_find.side_effect = [{"strike": 1, "instrument_id": 11}, {"strike": 2, "instrument_id": 22}]
        client = MagicMock()
        client.interactive.TRANSACTION_TYPE_BUY = "BUY"
        client.interactive.PRODUCT_MIS = "MIS"
        client.get_available_margin.return_value = 1_000_000
        client.get_ltp_map.return_value = {11: 2.0, 22: 2.0}
        client.place_market_order.side_effect = [101, None]
        client.cancel_order.side_effect = RuntimeError("cancel fail")
        strat = {"name": "S", "lots": 10}
        self.assertFalse(ensure_margin_or_skip_strategy(client, _index_cfg(), "17Mar2026", strat, 22000))

    @patch("trading.utils.get_today_strategies")
    @patch("trading.utils.INDEX_CONFIGS")
    def test_pick_index_sensex_tie(self, mock_cfgs, mock_today):
        nifty = MagicMock(name="NIFTY")
        nifty.name = "NIFTY"
        sensex = MagicMock(name="SENSEX")
        sensex.name = "SENSEX"
        mock_cfgs.values.return_value = [nifty, sensex]
        mock_cfgs.__getitem__.side_effect = lambda k: sensex if k == "SENSEX" else nifty
        mock_today.return_value = [MagicMock()]
        client = MagicMock()
        d = datetime.datetime(2026, 2, 12)
        client.get_expiry_dates.side_effect = [[d], [d]]
        client.format_expiry_for_options.return_value = "12FEB2026"
        cfg, _ = pick_index_and_expiry(client)
        self.assertEqual(cfg.name, "SENSEX")

    @patch("bot.should_execute_now", return_value=(False, "volatile", None))
    @patch("trading.strategy.executor.upsert_strategy_waiting_for_calm", return_value=5)
    @patch("bot.update_strategy")
    def test_execute_waits_for_calm_with_db_id(self, mock_upd, *_):
        client = MagicMock()
        client.interactive.TRANSACTION_TYPE_SELL = "SELL"
        client.interactive.PRODUCT_MIS = "MIS"
        strat = {"name": "S", "status": "PENDING", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20, "db_id": 3}
        execute_strategy(client, _index_cfg(), "12FEB2026", strat, force=True)
        self.assertTrue(any(c.kwargs.get("db_id") == 5 for c in mock_upd.call_args_list))

    @patch("trading.strategy.executor.get_ist_now")
    @patch("bot.should_execute_now", return_value=(True, "calm", {}))
    def test_execute_strategy_slotted_journal(self, _gate, mock_now):
        mock_now.return_value.strftime.return_value = "09:05:00"
        client = MagicMock()
        execute_strategy(
            client, _index_cfg(), "12FEB2026",
            {"name": "S", "status": "PENDING", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20},
            force=False,
        )

    @patch("bot.should_execute_now", return_value=(True, "calm", {}))
    @patch("bot._get_atm_strike", return_value=22000)
    @patch("trading.strategy.executor.is_expiry_day", return_value=False)
    @patch("trading.strategy.executor.USE_PREMIUM_BASED_STRIKE", False)
    @patch("bot.update_strategy")
    def test_execute_instruments_missing(self, *_):
        client = MagicMock()
        client.get_option_instrument_id.side_effect = [None, 22]
        execute_strategy(
            client, _index_cfg(), "12FEB2026",
            {"name": "S", "status": "PENDING", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20},
            force=True,
        )

    @patch("bot.should_execute_now", return_value=(True, "calm", {}))
    @patch("bot._get_atm_strike", return_value=22000)
    @patch("trading.strategy.executor.get_trading_flag_or", return_value=True)
    @patch("trading.strategy.executor.is_expiry_day", return_value=False)
    @patch("bot._ensure_margin_or_skip_strategy", return_value=True)
    @patch("trading.strategy.executor.complete_entry_with_sl_protection")
    @patch("bot.update_strategy")
    @patch("trading.strategy.executor.log_strategy_execution", return_value=1)
    @patch("trading.strategy.executor.log_order")
    @patch("time.time", return_value=1)
    @patch("time.sleep")
    def test_execute_premium_nifty_path(self, _sleep, _time, _log, _log_strat, _upd, mock_prot, *_m):
        client = MagicMock()
        client.interactive.TRANSACTION_TYPE_SELL = "SELL"
        client.interactive.PRODUCT_MIS = "MIS"
        with patch("trading.strategy.executor.find_strike_by_premium") as fs:
            fs.side_effect = [(22000, 1), (22000, 2)]
            client.get_ltp_map.return_value = {1: 1.0, 2: 1.0}
            client.place_market_order.side_effect = [1, 2]
            mock_prot.return_value = SlProtectionResult(
                ok=True, sl_orders=[{"a": 1}], positions=[{"p": 1}], sl_tag_map={"t": 1},
            )
            execute_strategy(
                client, _index_cfg("NIFTY"), "12FEB2026",
                {"name": "S", "status": "PENDING", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20},
                force=True,
            )

    @patch("bot.should_execute_now", return_value=(True, "calm", {}))
    @patch("bot._get_atm_strike", return_value=22000)
    @patch("trading.strategy.executor.get_trading_flag_or", return_value=True)
    @patch("trading.strategy.executor.is_expiry_day", return_value=False)
    @patch("trading.strategy.executor.find_strike_by_premium", return_value=None)
    @patch("bot.update_strategy")
    def test_execute_premium_strike_miss(self, *_):
        execute_strategy(
            MagicMock(), _index_cfg(), "12FEB2026",
            {"name": "S", "status": "PENDING", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20},
            force=True,
        )

    def test_utils_hedge_orders_loop(self):
        STRATEGY_STATE.clear()
        STRATEGY_STATE["S"] = {
            "hedge_orders": [
                {"side": "PE", "quantity": 5},
                {"side": "CE", "quantity": "bad"},
                {"side": "CE", "quantity": 0},
                {"side": "XX", "quantity": 1},
            ]
        }
        pe_h, ce_h = bot_tracked_hedge_buy_qty_by_side()
        self.assertEqual(pe_h, 5)
        self.assertEqual(ce_h, 0)

    def test_close_skip_non_matching_iid(self):
        client = MagicMock()
        close_positions_for_instruments(
            client, _index_cfg(), [{"ExchangeInstrumentId": 99, "Quantity": -1}], [1],
        )
        client.place_market_order.assert_not_called()

    def test_find_strike_no_best_match(self):
        client = MagicMock()
        client.get_option_instrument_id.return_value = 1
        client.get_ltp_map.return_value = {1: 999.0}
        self.assertIsNone(find_strike_by_premium(client, _index_cfg(), "12FEB2026", "CE", 22000, 150, 100, 200, max_steps=1))

    @patch("bot.USE_CALM_ZONE_GATEKEEPER", True)
    @patch("bot.CALM_ZONE_GATEKEEPER_MODE", "current_or_prior_calm")
    @patch("bot.fetch_last_two_spot_bar_rows")
    def test_gatekeeper_single_row_volatile(self, mock_two):
        mock_two.return_value = [{"range_5m": 999, "body_range_ratio": 0.9, "bar_time": "t1"}]
        ok, reason, _ = should_execute_now("S", "NIFTY")
        self.assertFalse(ok)

    def test_calm_blurb_all_fields(self):
        row = {"bar_time": "t", "prior_bar_time": "p", "range_5m": 1, "body_range_ratio": 0.1}
        blurb = calm_gatekeeper_context_blurb(row)
        self.assertIn("prior=p", blurb)
        self.assertIn("ratio=", blurb)

    @patch("bot.should_execute_now", return_value=(True, "calm", {}))
    @patch("bot._get_atm_strike", return_value=22000)
    @patch("trading.strategy.executor.is_expiry_day", return_value=False)
    @patch("trading.strategy.executor.USE_PREMIUM_BASED_STRIKE", False)
    @patch("bot._ensure_margin_or_skip_strategy", return_value=True)
    @patch("bot.update_strategy")
    @patch("time.time", return_value=1)
    def test_execute_rollback_exception(self, *_):
        client = MagicMock()
        client.interactive.TRANSACTION_TYPE_SELL = "SELL"
        client.interactive.PRODUCT_MIS = "MIS"
        client.get_option_instrument_id.side_effect = [11, 22]
        client.get_ltp_map.return_value = {11: 1.0, 22: 1.0}
        client.place_market_order.side_effect = [1001, None]
        client.get_positions.side_effect = RuntimeError("pos fail")
        execute_strategy(
            client, _index_cfg(), "12FEB2026",
            {"name": "S", "status": "PENDING", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20},
            force=True,
        )

    @patch("bot.HEDGE_ON_EVERY_STRATEGY", False)
    @patch("trading.strategy.margin.update_strategy")
    @patch("trading.strategy.margin.update_portfolio_margin")
    @patch("trading.strategy.margin.time.sleep")
    @patch("trading.strategy.margin.is_expiry_day", return_value=False)
    @patch("trading.strategy.margin.find_hedge_by_target_premium")
    def test_margin_low_margin_hedges_open_shorts(self, mock_find, *_):
        STRATEGY_STATE["OPEN"] = {
            "positions": [{"symbol": "NIFTY24MAR22000CE", "quantity": -130, "exit_price": None}],
        }
        mock_find.return_value = {"strike": 1, "instrument_id": 11}
        client = MagicMock()
        client.interactive.TRANSACTION_TYPE_BUY = "BUY"
        client.interactive.PRODUCT_MIS = "MIS"
        client.get_available_margin.side_effect = [500_000, 2_000_000]
        client.get_ltp_map.return_value = {11: 5.0}
        client.place_market_order.return_value = 101
        strat = {"name": "S", "lots": 10}
        self.assertTrue(ensure_margin_or_skip_strategy(client, _index_cfg(), "18Mar2026", strat, 22000))

    def test_find_hedge_pe_direction(self):
        client = MagicMock()
        client.get_option_instrument_id.return_value = 55
        client._token_meta = {55: {"ltp_hint": 5.0}}
        out = find_hedge_by_target_premium(client, _index_cfg(), "12FEB2026", "PE", 22000, 5, 3, 10, max_steps=1)
        self.assertIsNotNone(out)

    def test_is_expiry_day_invalid(self):
        self.assertFalse(is_expiry_day("not-a-date"))
        self.assertEqual(normalize_strategy_time_hhmmss("09:05:07"), "09:05:07")

    @patch("bot.HEDGE_ON_EVERY_STRATEGY", False)
    @patch("trading.strategy.margin.update_strategy")
    @patch("trading.strategy.margin.update_portfolio_margin")
    @patch("trading.strategy.margin.is_expiry_day", return_value=False)
    def test_margin_need_hedge_but_zero_qty(self, *_):
        client = MagicMock()
        client.get_available_margin.return_value = 5_000_000
        strat = {"name": "S", "lots": 2}
        self.assertTrue(ensure_margin_or_skip_strategy(client, _index_cfg(), "18Mar2026", strat, 22000))

    @patch("trading.strategy.gatekeeper.get_ist_now")
    @patch("bot.should_execute_now", return_value=(False, "volatile", {}))
    @patch("bot.update_strategy")
    def test_process_waiting_bad_next_check(self, _upd, _gate, mock_now):
        mock_now.return_value = datetime.datetime(2026, 4, 8, 9, 25, 0)
        STRATEGY_STATE["S"] = {
            "name": "S",
            "status": "WAITING_FOR_CALM",
            "gatekeeper_started_at": "2026-04-08T09:21:00",
            "next_gatekeeper_check_at": "bad",
        }
        process_waiting_for_calm(MagicMock(), MagicMock(), "10APR2026", lambda *a, **k: None)

    def test_process_waiting_skips_non_waiting(self):
        STRATEGY_STATE["S"] = {"name": "S", "status": "OPEN"}
        process_waiting_for_calm(MagicMock(), MagicMock(), "10APR2026", lambda *a, **k: None)

    @patch("bot.USE_CALM_ZONE_GATEKEEPER", True)
    @patch("bot.CALM_ZONE_GATEKEEPER_MODE", "recent_calm")
    @patch("bot.CALM_ZONE_RECENT_CALM_MINUTES", 10)
    @patch("bot.fetch_recent_calm_spot_row", return_value={"is_calmzone": 1})
    @patch("bot.fetch_latest_spot_bar_row", return_value=None)
    def test_recent_calm_no_latest(self, *_):
        ok, reason, _ = should_execute_now("S", "NIFTY")
        self.assertTrue(ok)
        self.assertEqual(reason, "calm_recent")

    @patch("bot.USE_CALM_ZONE_GATEKEEPER", True)
    @patch("bot.CALM_ZONE_GATEKEEPER_MODE", "recent_calm")
    @patch("bot.fetch_recent_calm_spot_row", return_value=None)
    @patch("bot.fetch_latest_spot_bar_row", return_value=None)
    def test_recent_calm_no_data(self, *_):
        ok, reason, row = should_execute_now("S", "NIFTY")
        self.assertFalse(ok)
        self.assertEqual(reason, "no_data")

    def test_spot_row_sensex_threshold(self):
        self.assertTrue(spot_row_is_calm({"range_5m": 100, "body_range_ratio": 0.1}, "SENSEX"))

    def test_compute_effective_lots_zero_planned(self):
        eff, msg = compute_effective_lots_from_margin(0, 1000, 150000, 0, 200000)
        self.assertEqual(eff, 0)

    def test_spot_row_is_calm_type_error(self):
        self.assertFalse(spot_row_is_calm({"range_5m": "bad", "body_range_ratio": 0.1}, "NIFTY"))

    def test_normalize_strategy_time_invalid(self):
        self.assertIsNone(normalize_strategy_time_hhmmss("xx:yy"))

    @patch("trading.strategy.gatekeeper.get_ist_now")
    def test_strategy_slot_datetime(self, mock_now):
        mock_now.return_value = datetime.datetime(2026, 4, 8, 10, 0, 0)
        dt = strategy_slot_ist_datetime({"time": "09:21:00"})
        self.assertEqual(dt.minute, 21)

    @patch("bot.HEDGE_ON_EVERY_STRATEGY", False)
    @patch("trading.strategy.margin.update_strategy")
    @patch("trading.strategy.margin.update_portfolio_margin")
    @patch("trading.strategy.margin.is_expiry_day", return_value=False)
    @patch("trading.strategy.margin.find_hedge_by_target_premium")
    def test_margin_pe_only_hedge(self, mock_find, *_):
        STRATEGY_STATE["X"] = {
            "positions": [{"symbol": "NIFTY24MAR22000CE", "quantity": -65, "exit_price": None}],
        }
        mock_find.return_value = {"strike": 1, "instrument_id": 11}
        client = MagicMock()
        client.interactive.TRANSACTION_TYPE_BUY = "BUY"
        client.interactive.PRODUCT_MIS = "MIS"
        client.get_available_margin.side_effect = [400_000, 2_000_000]
        client.get_ltp_map.return_value = {11: 5.0}
        client.place_market_order.return_value = 101
        strat = {"name": "S", "lots": 10}
        with patch("trading.strategy.margin.time.sleep"):
            self.assertTrue(ensure_margin_or_skip_strategy(client, _index_cfg(), "18Mar2026", strat, 22000))

    def test_find_strike_updates_best(self):
        client = MagicMock()
        client.get_option_instrument_id.side_effect = [1, 2, 3]
        client.get_ltp_map.return_value = {1: 140.0, 2: 155.0, 3: 145.0}
        out = find_strike_by_premium(client, _index_cfg(), "12FEB2026", "CE", 22000, 150, 100, 200, max_steps=1)
        self.assertIsNotNone(out)

    @patch("trading.utils.get_today_strategies", return_value=[MagicMock()])
    @patch("trading.utils.INDEX_CONFIGS")
    def test_pick_index_single_scheduled(self, mock_cfgs, _mock_today):
        cfg = MagicMock()
        cfg.name = "NIFTY"
        mock_cfgs.values.return_value = [cfg]
        client = MagicMock()
        client.get_expiry_dates.return_value = [datetime.datetime(2026, 2, 12)]
        client.format_expiry_for_options.return_value = "12FEB2026"
        _, exp = pick_index_and_expiry(client)
        self.assertEqual(exp, "12FEB2026")

    def test_find_hedge_skips_bad_ltp(self):
        client = MagicMock()
        client.get_option_instrument_id.return_value = 1
        client._token_meta = {1: {"ltp_hint": None}}
        self.assertIsNone(find_hedge_by_target_premium(client, _index_cfg(), "12FEB2026", "CE", 22000, 5, 1, 10, max_steps=1))

    def test_bot_tracked_invalid_qty_and_side(self):
        STRATEGY_STATE.clear()
        STRATEGY_STATE["S"] = {
            "positions": [{"symbol": "NIFTY24PE", "quantity": "bad", "exit_price": None}],
            "hedge_orders": [{"side": "PE", "quantity": 2}],
        }
        ce, pe = bot_tracked_open_short_qty_by_side()
        self.assertEqual(ce, 0)
        self.assertEqual(pe, 0)

    @patch("bot.should_execute_now", return_value=(True, "calm", {}))
    @patch("bot._get_atm_strike", return_value=82000)
    @patch("trading.strategy.executor.get_trading_flag_or", return_value=True)
    @patch("trading.strategy.executor.is_expiry_day", return_value=False)
    @patch("bot._ensure_margin_or_skip_strategy", return_value=True)
    @patch("trading.strategy.executor.complete_entry_with_sl_protection")
    @patch("trading.strategy.executor.update_strategy")
    @patch("trading.strategy.executor.log_strategy_execution", return_value=1)
    @patch("trading.strategy.executor.log_order")
    @patch("time.time", return_value=1)
    @patch("time.sleep")
    def test_execute_sensex_premium_branch(self, _sleep, _time, _log, _log_strat, _upd, mock_prot, *_m):
        cfg = _index_cfg("SENSEX")
        client = MagicMock()
        client.interactive.TRANSACTION_TYPE_SELL = "SELL"
        client.interactive.PRODUCT_MIS = "MIS"
        with patch("trading.strategy.executor.find_strike_by_premium") as fs:
            fs.side_effect = [(82000, 1), (82000, 2)]
            client.get_ltp_map.return_value = {1: 1.0, 2: 1.0}
            client.place_market_order.side_effect = [1, 2]
            mock_prot.return_value = SlProtectionResult(
                ok=True, sl_orders=[{"a": 1}], positions=[{"p": 1}], sl_tag_map={"t": 1},
            )
            execute_strategy(
                client, cfg, "12FEB2026",
                {"name": "S", "status": "PENDING", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20},
                force=True,
            )

    def test_spot_row_is_calm_is_calmzone_int(self):
        self.assertFalse(spot_row_is_calm({"is_calmzone": 0}, "NIFTY"))

    @patch("bot.USE_CALM_ZONE_GATEKEEPER", True)
    @patch("bot.CALM_ZONE_GATEKEEPER_MODE", "invalid_mode")
    @patch("bot.fetch_last_two_spot_bar_rows", return_value=[{"is_calmzone": 1}])
    def test_gatekeeper_invalid_mode(self, *_):
        ok, _, _ = should_execute_now("S", "NIFTY")
        self.assertTrue(ok)

    @patch("bot.USE_CALM_ZONE_GATEKEEPER", True)
    @patch("bot.CALM_ZONE_GATEKEEPER_MODE", "current_or_prior_calm")
    @patch("bot.fetch_last_two_spot_bar_rows")
    def test_gatekeeper_prior_only_no_prior_key(self, mock_two):
        mock_two.return_value = [
            {"range_5m": 999, "body_range_ratio": 0.9},
            {"range_5m": 40, "body_range_ratio": 0.1},
        ]
        ok, reason, _ = should_execute_now("S", "NIFTY")
        self.assertEqual(reason, "calm_prior")

    @patch("trading.strategy.gatekeeper.get_ist_now")
    @patch("bot.CALM_ZONE_WAIT_TIMEOUT_MINUTES", 30)
    @patch("bot.update_strategy")
    def test_gatekeeper_timeout_skip_no_db(self, _upd, mock_now):
        mock_now.return_value = datetime.datetime(2026, 4, 8, 11, 0, 0)
        STRATEGY_STATE["S"] = {
            "name": "S",
            "status": "WAITING_FOR_CALM",
            "gatekeeper_started_at": "2026-04-08T09:21:00",
            "next_gatekeeper_check_at": 0,
        }
        process_waiting_for_calm(MagicMock(), MagicMock(), "10APR2026", lambda *a, **k: None)

    @patch("bot.HEDGE_ON_EVERY_STRATEGY", False)
    @patch("trading.strategy.margin.update_strategy")
    @patch("trading.strategy.margin.update_portfolio_margin")
    @patch("trading.strategy.margin.is_expiry_day", return_value=False)
    @patch("trading.strategy.margin.find_hedge_by_target_premium")
    def test_margin_ce_hedge_only(self, mock_find, *_):
        STRATEGY_STATE["X"] = {
            "positions": [{"symbol": "NIFTY24MAR22000PE", "quantity": -65, "exit_price": None}],
        }
        mock_find.return_value = {"strike": 2, "instrument_id": 22}
        client = MagicMock()
        client.interactive.TRANSACTION_TYPE_BUY = "BUY"
        client.interactive.PRODUCT_MIS = "MIS"
        client.get_available_margin.side_effect = [400_000, 2_000_000]
        client.get_ltp_map.return_value = {22: 5.0}
        client.place_market_order.return_value = 101
        with patch("trading.strategy.margin.time.sleep"):
            self.assertTrue(
                ensure_margin_or_skip_strategy(client, _index_cfg(), "18Mar2026", {"name": "S", "lots": 10}, 22000)
            )

    def test_compute_effective_lots_gap_zero(self):
        eff, msg = compute_effective_lots_from_margin(5, 750_000, 150_000, 0, 200_000)
        self.assertEqual(eff, 4)
        self.assertIsNotNone(msg)

    def test_bot_tracked_pe_symbol(self):
        STRATEGY_STATE.clear()
        STRATEGY_STATE["S"] = {"positions": [{"symbol": "NIFTY24MAR22000PE", "quantity": -1, "exit_price": None}]}
        _, pe = bot_tracked_open_short_qty_by_side()
        self.assertEqual(pe, 1)

    def test_bot_tracked_hedge_ce_branch(self):
        STRATEGY_STATE.clear()
        STRATEGY_STATE["S"] = {"hedge_orders": [{"side": "CE", "quantity": 3}]}
        _, ce_h = bot_tracked_hedge_buy_qty_by_side()
        self.assertEqual(ce_h, 3)

    def test_find_hedge_invalid_instrument_type(self):
        client = MagicMock()
        client.get_option_instrument_id.return_value = object()
        self.assertIsNone(find_hedge_by_target_premium(client, _index_cfg(), "12FEB2026", "CE", 22000, 5, 1, 10, max_steps=1))

    def test_find_strike_ltp_none(self):
        client = MagicMock()
        client.get_option_instrument_id.return_value = 1
        client.get_ltp_map.return_value = {1: None}
        self.assertIsNone(find_strike_by_premium(client, _index_cfg(), "12FEB2026", "CE", 22000, 150, 100, 200, max_steps=0))

    @patch("bot.should_execute_now", return_value=(True, "calm", {}))
    @patch("bot._get_atm_strike", return_value=22000)
    @patch("trading.strategy.executor.is_expiry_day", return_value=False)
    @patch("trading.strategy.executor.USE_PREMIUM_BASED_STRIKE", False)
    @patch("bot._ensure_margin_or_skip_strategy", return_value=True)
    @patch("trading.strategy.executor.complete_entry_with_sl_protection")
    @patch("trading.strategy.executor.update_strategy")
    @patch("trading.strategy.executor.log_strategy_execution", return_value=9)
    @patch("trading.strategy.executor.log_order")
    @patch("time.time", return_value=1)
    @patch("time.sleep")
    def test_execute_protection_updates_sl_state(self, _s, _t, _lo, _ls, mock_upd, mock_prot, *_):
        client = MagicMock()
        client.interactive.TRANSACTION_TYPE_SELL = "SELL"
        client.interactive.PRODUCT_MIS = "MIS"
        client.get_option_instrument_id.side_effect = [11, 22]
        client.get_ltp_map.return_value = {11: 1.0, 22: 1.0}
        client.place_market_order.side_effect = [1001, 1002]
        mock_prot.return_value = SlProtectionResult(
            ok=True,
            sl_orders=[{"app_order_id": 9}],
            positions=[{"instrument_id": 11}],
            sl_tag_map={"t": 11},
        )
        execute_strategy(
            client, _index_cfg(), "12FEB2026",
            {"name": "S", "status": "PENDING", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20},
            force=True,
        )
        self.assertTrue(any("sl_orders" in c.kwargs for c in mock_upd.call_args_list))

    def test_positions_exposure_zero_qty(self):
        exp = positions_exposure_for_instruments([{"ExchangeInstrumentId": 1, "Quantity": 0}], [1])
        self.assertEqual(exp, {})

    def test_spot_row_is_calm_none(self):
        self.assertFalse(spot_row_is_calm(None, "NIFTY"))

    def test_normalize_empty_time(self):
        self.assertIsNone(normalize_strategy_time_hhmmss(""))

    @patch("bot.USE_CALM_ZONE_GATEKEEPER", True)
    @patch("bot.CALM_ZONE_GATEKEEPER_MODE", "current_or_prior_calm")
    @patch("bot.fetch_last_two_spot_bar_rows", return_value=[])
    def test_should_execute_no_data(self, *_):
        ok, reason, row = should_execute_now("S", "NIFTY")
        self.assertFalse(ok)
        self.assertEqual(reason, "no_data")
        self.assertIsNone(row)

    @patch("trading.utils.get_today_strategies", side_effect=[[], [MagicMock()]])
    @patch("trading.utils.INDEX_CONFIGS")
    def test_pick_index_skips_empty_schedule(self, mock_cfgs, mock_today):
        a, b = MagicMock(), MagicMock()
        a.name, b.name = "NIFTY", "SENSEX"
        mock_cfgs.values.return_value = [a, b]
        client = MagicMock()
        client.get_expiry_dates.return_value = [datetime.datetime(2026, 2, 12)]
        client.format_expiry_for_options.return_value = "12FEB2026"
        _, exp = pick_index_and_expiry(client)
        self.assertEqual(exp, "12FEB2026")

    @patch("bot.should_execute_now", return_value=(False, "volatile", None))
    @patch("trading.strategy.executor.upsert_strategy_waiting_for_calm", return_value=7)
    @patch("bot.update_strategy")
    def test_execute_calm_wait_persists_db_id(self, mock_upd, *_):
        client = MagicMock()
        execute_strategy(
            client, _index_cfg(), "12FEB2026",
            {"name": "S", "status": "PENDING", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20},
            force=True,
        )
        self.assertTrue(any(c.kwargs.get("db_id") == 7 for c in mock_upd.call_args_list))

        client = MagicMock()
        execute_strategy(
            client, _index_cfg(), "12FEB2026",
            {"name": "S", "status": "PENDING", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20},
            force=True,
        )
        self.assertTrue(any(c.kwargs.get("db_id") == 7 for c in mock_upd.call_args_list))

    @patch("bot.HEDGE_ON_EVERY_STRATEGY", True)
    @patch("trading.strategy.margin.update_strategy")
    @patch("trading.strategy.margin.update_portfolio_margin")
    @patch("trading.strategy.margin.is_expiry_day", return_value=False)
    @patch("bot._find_hedge_by_target_premium", return_value=None)
    def test_margin_hedge_block_no_placed_now(self, *_):
        client = MagicMock()
        client.get_available_margin.return_value = 5_000_000
        self.assertFalse(
            ensure_margin_or_skip_strategy(client, _index_cfg(), "18Mar2026", {"name": "S", "lots": 2}, 22000)
        )

    def test_find_strike_bad_ltp_value(self):
        client = MagicMock()
        client.get_option_instrument_id.return_value = 1
        client.get_ltp_map.return_value = {1: "nope"}
        self.assertIsNone(find_strike_by_premium(client, _index_cfg(), "12FEB2026", "CE", 22000, 150, 100, 200, max_steps=0))

    def test_gatekeeper_disabled_direct(self):
        with patch("bot.USE_CALM_ZONE_GATEKEEPER", False):
            ok, reason, row = should_execute_now("S", "NIFTY")
            self.assertTrue(ok)
            self.assertEqual(reason, "gatekeeper_disabled")
            self.assertIsNone(row)

    def test_find_hedge_bad_ltp_float(self):
        client = MagicMock()
        client.get_option_instrument_id.return_value = 1
        client._token_meta = {1: {"ltp_hint": object()}}
        self.assertIsNone(find_hedge_by_target_premium(client, _index_cfg(), "12FEB2026", "CE", 22000, 5, 1, 10, max_steps=1))

    def test_hedge_side_qty_ce_from_orders(self):
        STRATEGY_STATE.clear()
        STRATEGY_STATE["H"] = {"hedge_orders": [{"side": "CE", "quantity": 7}]}
        _, ce = bot_tracked_hedge_buy_qty_by_side()
        self.assertEqual(ce, 7)

    def test_hedge_side_qty_dict_bad_ce(self):
        STRATEGY_STATE.clear()
        STRATEGY_STATE["H"] = {"hedge_side_qty": {"PE": 1, "CE": "bad"}}
        pe, ce = bot_tracked_hedge_buy_qty_by_side()
        self.assertEqual(pe, 1)
        self.assertEqual(ce, 0)

    @patch("bot.should_execute_now", return_value=(True, "calm", {}))
    @patch("bot._get_atm_strike", return_value=22000)
    @patch("trading.strategy.executor.is_expiry_day", return_value=False)
    @patch("trading.strategy.executor.USE_PREMIUM_BASED_STRIKE", False)
    @patch("bot._ensure_margin_or_skip_strategy", return_value=True)
    @patch("trading.strategy.executor.complete_entry_with_sl_protection")
    @patch("trading.strategy.executor.update_strategy")
    @patch("trading.strategy.executor.log_strategy_execution", return_value=9)
    @patch("trading.strategy.executor.log_order")
    @patch("time.time", return_value=1)
    @patch("time.sleep")
    def test_execute_protection_fail_returns_early(self, _s, _t, _lo, _ls, mock_upd, mock_prot, *_):
        client = MagicMock()
        client.interactive.TRANSACTION_TYPE_SELL = "SELL"
        client.interactive.PRODUCT_MIS = "MIS"
        client.get_option_instrument_id.side_effect = [11, 22]
        client.get_ltp_map.return_value = {11: 1.0, 22: 1.0}
        client.place_market_order.side_effect = [1001, 1002]
        mock_prot.return_value = SlProtectionResult(ok=False, error_message="fail")
        execute_strategy(
            client, _index_cfg(), "12FEB2026",
            {"name": "S", "status": "PENDING", "time": "09:00:00", "lots": 1, "leg_sl_pct": 20},
            force=True,
        )
        self.assertFalse(any("sl_orders" in c.kwargs for c in mock_upd.call_args_list))


if __name__ == "__main__":
    unittest.main()
