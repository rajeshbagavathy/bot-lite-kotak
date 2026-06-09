import datetime
import functools
import json
import logging
import math
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import schedule


def _default_bot_log_path() -> str:
    """
    Log file next to this module (not cwd). Avoids bot vs UI reading different files when
    systemd/gunicorn cwd differs from where `python bot.py` was started.
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.log")


def _configure_bot_logging() -> None:
    """
    Configure logging before any Flask/ui imports.

    - Root logger: WARNING only (drops boto/botocore/urllib3 INFO noise).
    - ``xts-bot-lite``: INFO to stderr + bot.log file, propagate=False so app logs are isolated.
    - Werkzeug / boto / urllib3: effectively silent (CRITICAL, no propagate).
    """
    env_lp = os.environ.get("BOT_LOG_PATH")
    log_path = os.path.abspath(env_lp) if env_lp else _default_bot_log_path()
    # Same path for UI tail (`ui.read_bot_log_tail`) even if cwd differs between processes.
    os.environ["BOT_LOG_PATH"] = log_path
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    for h in root.handlers[:]:
        root.removeHandler(h)
    stderr_root = logging.StreamHandler(sys.stderr)
    stderr_root.setFormatter(fmt)
    root.addHandler(stderr_root)

    bot_log = logging.getLogger("xts-bot-lite")
    bot_log.handlers.clear()
    bot_log.setLevel(logging.INFO)
    bot_log.propagate = False
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    bot_log.addHandler(sh)
    try:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        bot_log.addHandler(fh)
    except OSError as e:
        sys.stderr.write(f"WARNING: Could not open {log_path} for logging: {e}\n")

    for _name in (
        "werkzeug",
        "werkzeug.serving",
        "boto3",
        "botocore",
        "botocore.credentials",
        "urllib3",
        "urllib3.connectionpool",
        "s3transfer",
    ):
        _lg = logging.getLogger(_name)
        _lg.handlers.clear()
        _lg.propagate = False
        _lg.setLevel(logging.CRITICAL)

    bot_log.debug("Bot logging: stderr (journald) + file %s (override with BOT_LOG_PATH)", log_path)


_configure_bot_logging()

from trading.journal import init_journal, Phase, record as journal_record
from trading.context import STRATEGY_STATE
from trading.strategy.executor import execute_strategy as _execute_strategy_modular
from trading.strategy.gatekeeper import process_waiting_for_calm as _process_waiting_for_calm_modular
from trading.orders.sl import verify_sl_orders_live as _verify_sl_orders_live

from config import (
    INDEX_CONFIGS,
    HEDGE_PREMIUM_MAX_EXPIRY,
    HEDGE_PREMIUM_MAX_NON_EXPIRY,
    HEDGE_PREMIUM_MIN_EXPIRY,
    HEDGE_PREMIUM_MIN_NON_EXPIRY,
    HEDGE_TARGET_PREMIUM_EXPIRY,
    HEDGE_TARGET_PREMIUM_NON_EXPIRY,
    ITM_STRIKES_NIFTY,
    ITM_STRIKES_SENSEX,
    LEG_SL_PCT_NON_EXPIRY,
    LEG_TARGET_PCT,
    MARKETABLE_LIMIT_SLIPPAGE_PCT,
    MARGIN_BUFFER_EXPIRY,
    MARGIN_BUFFER_NON_EXPIRY,
    MARGIN_REQUIRED_PER_LOT_NON_EXPIRY,
    MARGIN_TIGHT_BUFFER_MIN,
    MIN_MARGIN_TO_TRADE,
    HEDGE_ON_EVERY_STRATEGY,
    margin_required_per_lot_expiry,
    PORTFOLIO_SL_LIMIT,
    SOURCE,
    STRATEGY_SL_ENABLED,
    SURVIVOR_SL_TO_COST_ENABLED,
    STRIKE_PREMIUM_BUFFER_NIFTY,
    STRIKE_PREMIUM_BUFFER_SENSEX,
    STRIKE_PREMIUM_TARGET_NIFTY,
    STRIKE_PREMIUM_TARGET_SENSEX,
    TRADE_NON_EXPIRY_DAY,
    USE_PREMIUM_BASED_STRIKE,
    DEMO_MODE,
    DB_ENABLE_MTM_SNAPSHOTS,
    USE_CALM_ZONE_GATEKEEPER,
    CALM_ZONE_GATEKEEPER_MODE,
    CALM_ZONE_RECENT_CALM_MINUTES,
    CALM_ZONE_WAIT_TIMEOUT_MINUTES,
    CALM_ZONE_POLL_SECONDS,
    CALM_ZONE_GATEKEEPER_POLL_SECONDS,
    get_basic_auth_creds,
    get_today_strategies,
    load_login_credentials,
)
from db import (
    init_db,
    log_strategy_execution,
    log_position,
    log_order,
    update_order_status,
    update_position_exit,
    log_trade_closed,
    log_mtm_snapshot,
    cleanup_old_data,
    cleanup_previous_day_data,
    mark_strategy_skipped_volatility_db,
    restore_todays_strategies,
    upsert_strategy_waiting_for_calm,
    get_ist_timestamp,
    get_ist_now,
    fetch_latest_spot_bar_row,
    fetch_last_two_spot_bar_rows,
    fetch_recent_calm_spot_row,
)
from mtm import (
    calculate_mtm,
    calculate_mtm_from_kotak_broker_pnl,
    calculate_mtm_kotak_amounts,
    calculate_strategy_mtm,
    mtm_position_breakdown,
)
from state import (
    get_mtm_snapshots_enabled,
    get_trading_flag_or,
    init_state,
    init_trading_flags,
    set_bot_runtime_flags,
    set_index,
    set_index_error,
    set_mtm_snapshots_enabled,
    set_spot,
    update_portfolio,
    update_portfolio_margin,
    update_strategy,
    replace_strategies,
)
from ui import create_app, ensure_http_access_not_logged
from brokers.factory import create_trading_client
from xts_client import marketable_limit_price

logger = logging.getLogger("xts-bot-lite")
APP_START_TIME = get_ist_now()
_LAST_MTM_LOG: Dict[str, float] = {}  # strategy_name -> last log timestamp (for throttling)
_LAST_PORTFOLIO_MTM_LOG: float = 0.0


def _strategy_state_entry(cfg) -> dict:
    return {
        "name": cfg.name,
        "time": cfg.time,
        "lots": cfg.lots,
        "leg_sl_pct": cfg.leg_sl_pct,
        "leg_target_pct": LEG_TARGET_PCT,
        "strategy_sl": cfg.strategy_sl,
        "status": "PENDING",
        "mtm": 0.0,
        "realized": 0.0,
        "unrealized": 0.0,
        "strike": None,
        "instrument_ids": [],
        "sl_orders": [],
        "positions": [],
        "order_tags": [],
        "entry_time": None,
        "message": None,
        "last_update": None,
        "sl_tag_map": {},
        "db_id": None,
        "survivor_sl_adjusted_to_cost": False,
        "survivor_sl_to_cost_hint": None,
        "scheduled_time": cfg.time,
        "gatekeeper_started_at": None,
        "next_gatekeeper_check_at": None,
        "skip_reason": None,
    }


def _merge_restored_strategy(restored_strategy: dict) -> Dict[str, Any]:
    st = restored_strategy.get("status", "OPEN")
    merge_kw: Dict[str, Any] = {
        "db_id": restored_strategy["db_id"],
        "status": st,
        "strike": restored_strategy["strike"],
        "entry_time": restored_strategy["entry_time"],
        "positions": restored_strategy["positions"],
        "sl_orders": restored_strategy.get("sl_orders") or [],
        "sl_tag_map": restored_strategy.get("sl_tag_map") or {},
        "gatekeeper_started_at": restored_strategy.get("gatekeeper_started_at"),
        "skip_reason": restored_strategy.get("skip_reason"),
    }
    if st == "WAITING_FOR_CALM":
        merge_kw["next_gatekeeper_check_at"] = time.time() - 1.0
    else:
        merge_kw["next_gatekeeper_check_at"] = None
    if st == "SKIPPED_VOLATILITY":
        sr = restored_strategy.get("skip_reason") or ""
        merge_kw["message"] = (
            f"Skipped — calm zone timeout ({sr})" if sr else "Skipped — calm zone timeout"
        )
    return merge_kw


def _load_strategy_state(client: Any, index_config) -> None:
    """Build STRATEGY_STATE for today's index and push to UI snapshot."""
    new_state = {
        cfg.name: _strategy_state_entry(cfg) for cfg in get_today_strategies(index_config.name)
    }
    STRATEGY_STATE.clear()
    STRATEGY_STATE.update(new_state)
    restore_order_book: Optional[List[dict]] = None
    if client is not None:
        try:
            restore_order_book = client.get_order_book()
        except Exception as e:
            logger.error("Failed to fetch order book for SL restore-link bootstrap: %s", e)
    for restored_strategy in restore_todays_strategies():
        strategy_name = restored_strategy["strategy_name"]
        if strategy_name not in STRATEGY_STATE:
            continue
        logger.debug("Restoring %s from database...", strategy_name)
        STRATEGY_STATE[strategy_name].update(_merge_restored_strategy(restored_strategy))
        if (
            STRATEGY_STATE[strategy_name].get("status") == "OPEN"
            and not STRATEGY_STATE[strategy_name].get("sl_orders")
        ):
            _rebuild_sl_links_from_order_book_for_restored_strategy(
                STRATEGY_STATE[strategy_name], restore_order_book
            )
    _reconcile_restored_strategies_for_restart()
    replace_strategies(STRATEGY_STATE)
    logger.info(
        "Strategy state loaded: %d slot(s) for %s",
        len(STRATEGY_STATE),
        index_config.name,
    )


def _pick_index_and_expiry(client: Any) -> Tuple[dict, str]:
    expiry_map = {}
    for config in INDEX_CONFIGS.values():
        if not get_today_strategies(config.name):
            logger.debug("  %s: no strategies scheduled today; skipping for index pick", config.name)
            continue
        expiries = client.get_expiry_dates(config)
        if expiries:
            nearest = min(expiries)
            expiry_map[config.name] = nearest
            logger.debug("  %s nearest active expiry: %s", config.name, nearest)

    if not expiry_map:
        scheduled = [c.name for c in INDEX_CONFIGS.values() if get_today_strategies(c.name)]
        if not scheduled:
            logger.error("No strategies scheduled for today (weekday/index plan empty)")
            raise RuntimeError("No strategies scheduled for today")
        logger.error("No expiries found for indices scheduled today: %s", scheduled)
        raise RuntimeError("No expiries found for NIFTY or SENSEX")

    earliest = min(expiry_map.values())
    candidates = [name for name, date in expiry_map.items() if date == earliest]
    chosen_name = "SENSEX" if "SENSEX" in candidates else candidates[0]
    expiry = client.format_expiry_for_options(earliest)
    logger.debug(f"Selected: {chosen_name} expiry: {expiry}")
    return INDEX_CONFIGS[chosen_name], expiry


def _get_atm_strike(client: Any, index_config) -> Optional[int]:
    spot = client.get_spot_ltp(index_config)
    if spot is None:
        return None
    spot_val = round(float(spot), 2)
    set_spot(spot_val)
    return int(round(spot_val / index_config.strike_diff) * index_config.strike_diff)


def _is_expiry_day(expiry: str) -> bool:
    """Return True if today (IST) equals the option expiry date."""
    try:
        expiry_date = datetime.datetime.strptime(expiry, "%d%b%Y").date()
    except ValueError:
        return False
    return get_ist_now().date() == expiry_date


def _find_hedge_by_target_premium(
    client: Any,
    index_config,
    expiry: str,
    option_type: str,
    atm_strike: int,
    target_premium: float,
    min_premium: float,
    max_premium: float,
    max_steps: int = 40,
) -> Optional[dict]:
    """Delegate to modular hedge search (in-memory chain LTP; no batch quote hang)."""
    from trading.strategy.strikes import find_hedge_by_target_premium

    return find_hedge_by_target_premium(
        client,
        index_config,
        expiry,
        option_type,
        atm_strike,
        target_premium,
        min_premium,
        max_premium,
        max_steps=max_steps,
    )


def _find_strike_by_premium(
    client: Any,
    index_config,
    expiry: str,
    option_type: str,
    atm_strike: int,
    target_premium: float,
    min_premium: float,
    max_premium: float,
    max_steps: int = 30,
) -> Optional[Tuple[int, int]]:
    """
    Find strike (and instrument_id) whose option LTP is within [min_premium, max_premium],
    and closest to target_premium. Scans ATM first, then OTM/ITM in both directions.
    Returns (strike, instrument_id) or None if no strike in range.
    """
    strike_diff = int(index_config.strike_diff)
    strikes_to_check: List[int] = [atm_strike]
    for i in range(1, max_steps + 1):
        strikes_to_check.append(atm_strike + i * strike_diff)
        strikes_to_check.append(atm_strike - i * strike_diff)

    candidates: List[Tuple[int, int]] = []
    for strike in strikes_to_check:
        instrument_id = client.get_option_instrument_id(
            index_config, expiry, option_type.upper(), strike
        )
        if instrument_id:
            candidates.append((strike, int(instrument_id)))

    if not candidates:
        return None

    instruments = [
        {"exchangeSegment": index_config.option_ltp_segment, "exchangeInstrumentID": iid}
        for _, iid in candidates
    ]
    ltp_map = client.get_ltp_map(instruments)

    best: Optional[Tuple[float, int, int]] = None  # (abs_diff, strike, instrument_id)
    for strike, instrument_id in candidates:
        ltp = ltp_map.get(instrument_id)
        if ltp is None:
            continue
        try:
            ltp_val = float(ltp)
        except (TypeError, ValueError):
            continue
        if not (min_premium <= ltp_val <= max_premium):
            continue
        diff = abs(ltp_val - target_premium)
        if best is None or diff < best[0]:
            best = (diff, strike, instrument_id)

    if best is None:
        return None
    _, strike, instrument_id = best
    return (strike, instrument_id)


def _bot_tracked_open_short_qty_by_side() -> Tuple[int, int]:
    from trading.utils import bot_tracked_open_short_qty_by_side

    return bot_tracked_open_short_qty_by_side()


def _bot_tracked_hedge_buy_qty_by_side() -> Tuple[int, int]:
    from trading.utils import bot_tracked_hedge_buy_qty_by_side

    return bot_tracked_hedge_buy_qty_by_side()


def _compute_effective_lots_from_margin(
    planned_lots: int,
    available_margin: Optional[float],
    per_lot_margin: float,
    buffer: float,
    tight_buffer: float,
) -> Tuple[int, Optional[str]]:
    """
    Compute executable lots with conservative margin policy:
    - gap > tight_buffer: use planned
    - 0 <= gap <= tight_buffer: reduce by 1
    - gap < 0: recompute affordable then reduce by 1
    """
    planned = max(int(planned_lots or 0), 0)
    if planned <= 0 or available_margin is None:
        return planned, None

    required = planned * float(per_lot_margin) + float(buffer)
    gap = float(available_margin) - required
    if gap > float(tight_buffer):
        return planned, None
    if gap >= 0:
        eff = max(planned - 1, 0)
        return eff, (
            f"Margin tight: available {available_margin:.0f}, required {required:.0f}, "
            f"cushion {gap:.0f} <= {tight_buffer:.0f}; reducing planned lots {planned} -> {eff}."
        )

    affordable = int(max((float(available_margin) - float(buffer)) / float(per_lot_margin), 0))
    affordable = min(affordable, planned)
    eff = max(affordable - 1, 0)
    if eff <= 0:
        # User intent: if at least ~₹2L is available, still try to execute 1 lot (if 1 lot is affordable).
        one_lot_required = float(per_lot_margin) + float(buffer)
        if float(available_margin) >= float(MIN_MARGIN_TO_TRADE) and float(available_margin) >= one_lot_required:
            return 1, (
                f"Margin low: available {available_margin:.0f}, required {required:.0f} for planned {planned}; "
                f"forcing minimum 1 lot (min_margin {MIN_MARGIN_TO_TRADE:.0f}, 1-lot required {one_lot_required:.0f})."
            )
    return eff, (
        f"Insufficient margin: available {available_margin:.0f}, required {required:.0f} for planned {planned}; "
        f"affordable {affordable}, conservative reduce by 1 -> execute {eff}."
    )


def _ensure_margin_or_skip_strategy(
    client: Any,
    index_config,
    expiry: str,
    strategy: dict,
    atm_strike: int,
) -> bool:
    """Delegate to modular margin gate (journal + fast hedge path)."""
    from trading.strategy.margin import ensure_margin_or_skip_strategy

    return ensure_margin_or_skip_strategy(client, index_config, expiry, strategy, atm_strike)


def _round_to_tick(price: float, tick_size: float = 0.05) -> float:
    """Round price to nearest tick size (required by XTS API)."""
    return math.ceil(price / tick_size) * tick_size


def _order_book_status_is_filled(status_raw: Optional[str]) -> bool:
    """
    XTS/broker may return FILLED, Complete, Traded, etc. Normalize for SL exit detection.
    """
    s = (status_raw or "").replace(" ", "").replace("_", "").upper()
    return s in (
        "FILLED",
        "COMPLETE",
        "COMPLETED",
        "TRADED",
        "CLOSED",
        "EXECUTED",
        "FULLYTRADED",
    )


# Closed-leg reasons that still imply one straddle leg is gone; tighten survivor SL to cost.
# RESTORED: exit_price loaded from DB on restart (closed_via not persisted in SQLite).
_SURVIVOR_PEER_CLOSED_VIA_OK = frozenset({"SL_FILLED", "BROKER_SYNC", "RESTORED"})


def _place_leg_sl_orders(
    client: Any,
    index_config,
    filled_orders: List[dict],
    leg_sl_pct: float,
    strategy_name: str,
) -> Tuple[List[dict], Dict[str, int]]:
    """Place SL orders and return (sl_orders, tag_to_instrument_map)"""
    sl_orders = []
    tag_to_instrument = {}
    for order in filled_orders:
        # Use traded quantity when available (handles partial fills safely)
        try:
            quantity = int(order.get("OrderQuantityTraded") or order.get("OrderQuantity") or 0)
        except (TypeError, ValueError):
            quantity = 0
        if quantity <= 0:
            continue

        # Calculate raw SL price
        raw_price = float(order["OrderAverageTradedPrice"]) * (1 + leg_sl_pct / 100.0)
        
        # Round to nearest tick size to comply with XTS API
        price = _round_to_tick(raw_price, index_config.tick_size)
        trigger = _round_to_tick(max(price - 0.5, 0.05), index_config.tick_size)
        
        # IMPORTANT: XTS/broker can truncate OrderUniqueIdentifier, so keep tags short and unique.
        # Use instrument_id to guarantee uniqueness across legs/strategies.
        instrument_id = int(order["ExchangeInstrumentID"])
        tag = f"{strategy_name}_SL_{instrument_id}"
        order_id = client.place_sl_order(
            index_config=index_config,
            instrument_id=instrument_id,
            order_side=client.interactive.TRANSACTION_TYPE_BUY,
            quantity=quantity,
            limit_price=price,
            stop_price=trigger,
            tag=tag,
            product_type=order["ProductType"],
        )
        if order_id:
            sl_orders.append({"app_order_id": order_id, "tag": tag})
            tag_to_instrument[tag] = instrument_id
            log_order(
                strategy_name,
                int(order_id) if order_id is not None else None,
                tag,
                instrument_id,
                order.get("TradingSymbol", ""),
                quantity,
                "STOPLIMIT",
                "BUY",
            )
    return sl_orders, tag_to_instrument


def _get_filled_orders(order_book: List[dict], app_order_ids: List[int]) -> List[dict]:
    id_set = {int(x) for x in (app_order_ids or []) if x is not None}
    return [
        order
        for order in order_book
        if int(order.get("AppOrderID") or 0) in id_set
        and str(order.get("OrderStatus", "")).replace(" ", "").upper()
        in ("FILLED", "PARTIALLYFILLED")
        and float(order.get("OrderAverageTradedPrice") or 0) > 0
    ]


def _calm_gatekeeper_context_blurb(row: Optional[dict]) -> str:
    """Human-readable latest (and optional prior) bar times for wait/volatile messages."""
    if not row:
        return "latest=none"
    parts = [f"latest={row.get('bar_time')}"]
    pt = row.get("prior_bar_time")
    if pt:
        parts.append(f"prior={pt}")
    return ", ".join(parts)


def _normalize_strategy_time_hhmmss(raw: str) -> Optional[str]:
    t = (raw or "").strip()
    if not t:
        return None
    c = t.count(":")
    if c == 1:
        return f"{t}:00"
    if c == 2:
        return t
    return None


def _strategy_slot_ist_datetime(strategy: dict) -> datetime.datetime:
    """Today's calendar date in IST with the strategy's slotted wall time."""
    now = get_ist_now()
    norm = _normalize_strategy_time_hhmmss(str(strategy.get("time") or ""))
    if not norm:
        return now
    try:
        hh, mm, ss = (int(x) for x in norm.split(":"))
    except ValueError:
        return now
    return now.replace(hour=hh, minute=mm, second=ss, microsecond=0)


def _gatekeeper_window_start_iso(strategy: dict) -> str:
    """ISO timestamp for the strategy slot (IST); used to anchor the calm wait timeout."""
    return _strategy_slot_ist_datetime(strategy).isoformat(timespec="seconds")


_STRATEGY_EXEC_LOCK = threading.RLock()

# Restart / catch-up may retry these within the slot + calm window (not OPEN/CLOSED).
_CATCH_UP_ENTRY_STATUSES = frozenset({"PENDING", "ERROR", "WAITING_FOR_CALM", "SKIPPED_VOLATILITY"})


def _strategy_entry_window(strategy: dict, now: Optional[datetime.datetime] = None) -> Tuple[datetime.datetime, datetime.datetime]:
    """Return (slot_start, window_end) in IST for the strategy's scheduled entry."""
    now = now or get_ist_now()
    slot_dt = _strategy_slot_ist_datetime(strategy)
    window_end = slot_dt + datetime.timedelta(minutes=int(CALM_ZONE_WAIT_TIMEOUT_MINUTES))
    return slot_dt, window_end


def _strategy_within_entry_window(strategy: dict, now: Optional[datetime.datetime] = None) -> bool:
    now = now or get_ist_now()
    slot_dt, window_end = _strategy_entry_window(strategy, now)
    return slot_dt <= now <= window_end


def _reconcile_restored_strategies_for_restart() -> None:
    """
    After DB restore on restart: if we are still inside the slot's 30-minute entry window,
    allow another attempt (clear SKIPPED/ERROR from earlier crash or unauthorized order).
    """
    now = get_ist_now()
    for name, strategy in list(STRATEGY_STATE.items()):
        if not _strategy_within_entry_window(strategy, now):
            continue
        st = strategy.get("status")
        if st in ("OPEN", "CLOSED", "DISABLED"):
            continue
        slot_iso = _gatekeeper_window_start_iso(strategy)
        if st == "SKIPPED_VOLATILITY":
            logger.info(
                "Strategy %s: restart inside entry window — clearing SKIPPED, will retry",
                name,
            )
            strategy.update(
                status="PENDING",
                message="Restart retry (was skipped for calm timeout)",
                gatekeeper_started_at=None,
                next_gatekeeper_check_at=None,
                skip_reason=None,
            )
        elif st == "ERROR":
            logger.info(
                "Strategy %s: restart inside entry window — clearing ERROR (%s)",
                name,
                (strategy.get("message") or "")[:120],
            )
            strategy.update(
                status="PENDING",
                message="Restart retry after previous error",
                gatekeeper_started_at=None,
                next_gatekeeper_check_at=None,
                skip_reason=None,
            )
        elif st == "WAITING_FOR_CALM":
            strategy["gatekeeper_started_at"] = slot_iso
            strategy["next_gatekeeper_check_at"] = time.time() - 1.0
            strategy["message"] = "Restart: resuming calm-zone wait from slot time"
            logger.info("Strategy %s: restart — reset calm gatekeeper to slot %s", name, slot_iso)


def _wait_for_kotak_trading_session(client: Any, timeout_sec: float = 20.0) -> bool:
    """Confirm Kotak session can read limits/order book before placing orders (avoids 100008 unauthorized)."""
    from config import BROKER_BACKEND

    if BROKER_BACKEND != "kotak":
        return True
    deadline = time.time() + timeout_sec
    ok_streak = 0
    while time.time() < deadline:
        try:
            client._ensure()
            net = client.get_available_margin()
            client.get_order_book()
            if net is not None:
                ok_streak += 1
                if ok_streak >= 2:
                    logger.info("Kotak trading session ready (margin=%s)", net)
                    return True
        except Exception as e:
            logger.debug("Kotak session wait: %s", e)
        time.sleep(1.0)
    logger.warning("Kotak trading session not confirmed within %.0fs — orders may fail", timeout_sec)
    return False


def _catch_up_missed_scheduled_strategies(client: Any, index_config, expiry: str) -> None:
    """
    ``schedule.every().day.at(slot)`` does not run a job if the process starts after that
    time today. Run eligible strategies once when the process starts inside
    ``[slot, slot + CALM_ZONE_WAIT_TIMEOUT_MINUTES]`` so restarts still honor the gatekeeper window.
    """
    if _SCHEDULER_MINIMAL_MODE:
        return
    now = get_ist_now()
    for strategy in STRATEGY_STATE.values():
        if strategy.get("status") not in _CATCH_UP_ENTRY_STATUSES:
            continue
        slot_dt, window_end = _strategy_entry_window(strategy, now)
        if now < slot_dt or now > window_end:
            continue
        logger.info(
            "Catch-up: running %s (slot %s, status %s; inside %s min window)",
            strategy["name"],
            strategy.get("time"),
            strategy.get("status"),
            CALM_ZONE_WAIT_TIMEOUT_MINUTES,
        )
        _execute_strategy(client, index_config, expiry, strategy, force=False)


def _spot_row_is_calm(row: Optional[dict], index_name: str) -> bool:
    """Match volatility monitor: use stored 5m metrics when present, else is_calmzone flag."""
    if not row:
        return False
    try:
        rg = row.get("range_5m")
        rt = row.get("body_range_ratio")
        if rg is not None and rt is not None:
            thr = 50.0 if str(index_name or "").upper() == "NIFTY" else 120.0
            return float(rg) < thr and float(rt) < 0.25
    except (TypeError, ValueError):
        pass
    return bool(int(row.get("is_calmzone") or 0))


def should_execute_now(strategy_id: str, index_name: str) -> Tuple[bool, str, Optional[dict]]:
    """
    Gatekeeper: when to allow entry.

    - ``current_or_prior_calm`` (default): newest 1m row or the immediately prior row is calm.
    - ``latest_bar``: only the newest 1m row must be calm.
    - ``recent_calm``: allow if any calm bar exists within CALM_ZONE_RECENT_CALM_MINUTES.
    """
    if not USE_CALM_ZONE_GATEKEEPER:
        return True, "gatekeeper_disabled", None
    mode = (CALM_ZONE_GATEKEEPER_MODE or "current_or_prior_calm").strip().lower()
    if mode not in ("latest_bar", "current_or_prior_calm", "recent_calm"):
        mode = "current_or_prior_calm"

    if mode == "latest_bar":
        latest = fetch_latest_spot_bar_row(index_name)
        if not latest:
            return False, "no_data", None
        is_calm = _spot_row_is_calm(latest, index_name)
        return (True, "calm", latest) if is_calm else (False, "volatile", latest)

    if mode == "current_or_prior_calm":
        rows = fetch_last_two_spot_bar_rows(index_name)
        if not rows:
            return False, "no_data", None
        latest = rows[0]
        prior = rows[1] if len(rows) > 1 else None
        if _spot_row_is_calm(latest, index_name):
            return True, "calm_current", latest
        if prior and _spot_row_is_calm(prior, index_name):
            return True, "calm_prior", prior
        ctx = dict(latest)
        if prior:
            ctx["prior_bar_time"] = prior.get("bar_time")
        return False, "volatile", ctx

    latest = fetch_latest_spot_bar_row(index_name)
    min_u = int(time.time()) - int(CALM_ZONE_RECENT_CALM_MINUTES) * 60
    calm_row = fetch_recent_calm_spot_row(index_name, min_u)
    if calm_row:
        return True, "calm_recent", calm_row
    if not latest:
        return False, "no_data", None
    return False, "volatile", latest


def _process_waiting_for_calm(client: Any, index_config, expiry: str) -> None:
    """Non-blocking retry path for strategies waiting on calm zone (modular + journal)."""
    _process_waiting_for_calm_modular(client, index_config, expiry, _execute_strategy)


def _execute_strategy(client: Any, index_config, expiry: str, strategy, force: bool = False) -> None:
    """Delegate to trading.strategy.executor (journal + SL lifecycle)."""
    _execute_strategy_modular(client, index_config, expiry, strategy, force=force)


# Legacy inline executor removed — see trading/strategy/executor.py


def _place_close_order(client: Any, index_config, pos: dict, tag_prefix: str) -> None:
    """Helper to place close/square-off order."""
    quantity = int(pos["Quantity"])
    if quantity == 0:
        return
    order_side = client.interactive.TRANSACTION_TYPE_BUY if quantity < 0 else client.interactive.TRANSACTION_TYPE_SELL
    client.place_market_order(
        index_config=index_config,
        instrument_id=int(pos["ExchangeInstrumentId"]),
        order_side=order_side,
        quantity=abs(quantity),
        tag=f"{tag_prefix}_{int(pos['ExchangeInstrumentId'])}_{int(time.time())}",
        product_type=pos["ProductType"],
    )


def _close_positions_for_instruments(
    client: Any, index_config, positions: List[dict], instrument_ids: List[int]
) -> None:
    for pos in positions:
        if int(pos["ExchangeInstrumentId"]) in instrument_ids:
            _place_close_order(client, index_config, pos, "CLOSE")


def _cancel_strategy_sl_orders(client: Any, strategy: dict) -> None:
    for sl_order in strategy.get("sl_orders", []) or []:
        try:
            client.cancel_order(sl_order["app_order_id"], sl_order["tag"])
        except Exception:
            logger.exception("Failed to cancel SL order %s", sl_order)


def _close_strategy_via_open_sl_orders(client: Any, index_config, strategy: dict) -> None:
    """
    Close strategy by converting open SL orders to marketable LIMIT execution.

    This approach:
    1. Checks order book status of each SL order
    2. If status = 'New'/'Replaced' → SL still open, modify to LIMIT at LTP +/- slippage
    3. If status = 'Filled' → Already closed by individual leg SL, skip
    4. If status = 'Cancelled'/'Rejected' → Failed, skip

    Benefit: Only closes still-open legs, avoids double-closing already-filled positions.
    This handles scenarios where one leg SL hit before strategy SL.
    """
    sl_orders = strategy.get("sl_orders", []) or []
    if not sl_orders:
        logger.debug(f"No SL orders found for strategy {strategy['name']}")
        return

    sl_tag_map = strategy.get("sl_tag_map", {}) or {}

    try:
        order_book = client.get_order_book()
    except Exception as e:
        logger.error(f"Failed to fetch order book for {strategy['name']}: {e}")
        return

    # Create map: app_order_id / tag → order details from order book
    order_book_map = {}
    for order in order_book:
        app_order_id = order.get("AppOrderID")
        tag = order.get("OrderUniqueIdentifier")
        if app_order_id or tag:
            order_book_map[app_order_id] = order
            order_book_map[tag] = order

    pending: List[Tuple[Any, Any, dict, Optional[int]]] = []
    for sl_order in sl_orders:
        app_order_id = sl_order.get("app_order_id")
        tag = sl_order.get("tag")

        order_detail = order_book_map.get(app_order_id) or order_book_map.get(tag)

        if not order_detail:
            logger.warning(
                f"SL order not found in order book: {strategy['name']} - {tag}"
            )
            continue

        order_status = order_detail.get("OrderStatus", "").upper()

        if order_status in ("NEW", "REPLACED"):
            iid: Optional[int] = None
            if tag and tag in sl_tag_map:
                try:
                    iid = int(sl_tag_map[tag])
                except (TypeError, ValueError):
                    iid = None
            if iid is None:
                for k in ("ExchangeInstrumentID", "ExchangeInstrumentId", "InstrumentID", "InstrumentId"):
                    v = order_detail.get(k)
                    if v is not None:
                        try:
                            iid = int(v)
                            break
                        except (TypeError, ValueError):
                            continue
            pending.append((app_order_id, tag, order_detail, iid))
        elif order_status == "FILLED":
            logger.debug(
                f"ℹ️  [{strategy['name']}] SL already FILLED, skipping: {tag}"
            )
        elif order_status in ("CANCELLED", "REJECTED"):
            logger.warning(
                f"⚠️  [{strategy['name']}] SL order {order_status}: {tag} "
                f"(position exposed, manual intervention may be needed)"
            )

    iids = [x[3] for x in pending if x[3] is not None]
    instruments = [
        {"exchangeSegment": index_config.option_ltp_segment, "exchangeInstrumentID": int(i)}
        for i in sorted(set(iids))
    ]
    ltp_batch = client.get_ltp_map(instruments) if instruments else {}

    for app_order_id, tag, order_detail, iid in pending:
        ltp = ltp_batch.get(int(iid)) if iid is not None else None
        if ltp is None:
            logger.warning(
                "[%s] No LTP for SL modify (tag=%s instrument=%s); skip LIMIT conversion",
                strategy["name"],
                tag,
                iid,
            )
            continue
        try:
            ltp_f = float(ltp)
        except (TypeError, ValueError):
            continue
        order_side = order_detail.get("OrderSide") or client.interactive.TRANSACTION_TYPE_BUY
        limit_price = marketable_limit_price(
            ltp_f,
            str(order_side),
            MARKETABLE_LIMIT_SLIPPAGE_PCT,
            float(index_config.tick_size),
        )
        try:
            client.modify_order(
                app_order_id=app_order_id,
                product_type=order_detail.get("ProductType"),
                order_type=client.interactive.ORDER_TYPE_LIMIT,
                quantity=int(order_detail.get("OrderQuantity", 0)),
                disclosed_quantity=int(order_detail.get("OrderDisclosedQuantity", 0)),
                stop_price=0,
                limit_price=float(limit_price),
                time_in_force=order_detail.get("TimeInForce"),
                tag=tag,
            )
            logger.debug(
                f"✅ [{strategy['name']}] Modified SL order to marketable LIMIT: {tag} "
                f"(Qty: {order_detail.get('OrderQuantity')}, limit={limit_price})"
            )
        except Exception as e:
            logger.error(
                f"Failed to modify SL order for {strategy['name']}: {tag} - {e}"
            )


def _close_strategy(client: Any, index_config, strategy: dict, positions: List[dict], reason: str) -> None:
    if strategy["status"] in ("CLOSED", "CLOSING"):
        return
    update_strategy(strategy["name"], status="CLOSING", message=reason)
    _close_strategy_via_open_sl_orders(client, index_config, strategy)
    
    # Log closed trade to database
    db_id = strategy.get("db_id")
    if db_id and db_id > 0:
        exit_time = get_ist_timestamp()
        log_trade_closed(
            strategy.get("name", ""),
            strategy.get("strike"),
            strategy.get("entry_time"),
            exit_time,
            strategy.get("realized", 0.0),
            strategy.get("mtm", 0.0),
            reason,
        )
    
    # Keep `positions` so UI can continue showing realized P&L for CLOSED strategies.
    # Clear SL order tracking to avoid reusing old SL orders/tags.
    update_strategy(strategy["name"], status="CLOSED", sl_orders=[], sl_tag_map={})


def _square_off_all(client: Any, index_config, positions: List[dict], reason: str) -> None:
    logger.warning("Square-off all positions: %s", reason)
    for pos in positions:
        _place_close_order(client, index_config, pos, "SQUAREOFF")


def _sync_sl_order_status_and_capture_exits(
    client: Any,
    strategy: dict,
    order_book: Optional[List[dict]] = None,
) -> None:
    """
    Monitor XTS order book to determine if SL orders are filled (position closure).
    This is the SINGLE SOURCE OF TRUTH for position status.
    
    Why order book?
    - Multiple strategies can hold same instruments (same strike CE/PE)
    - Broker positions are aggregated, can't distinguish per-strategy ownership
    - SL order tags are strategy-specific, unambiguous
    
    Logic:
    - For each position, find corresponding SL order from sl_orders
    - Query order book: what is SL order status?
    - If "Filled" → position is closed, capture exit_price
    - If "Pending/Rejected" → position status unchanged
    """
    if strategy["status"] != "OPEN" or not strategy.get("sl_orders"):
        return
    
    # Handle DEMO_MODE (client is None)
    if client is None:
        logger.debug(f"Skipping order book sync for {strategy['name']} (DEMO_MODE, no client)")
        return
    
    if order_book is None:
        try:
            order_book = client.get_order_book()
        except Exception as e:
            logger.error(f"Failed to fetch order book for {strategy['name']}: {e}")
            return
    
    # Create mapping: app_order_id / sl_order_tag → order details
    order_book_map_by_tag: Dict[str, dict] = {}
    order_book_map_by_id: Dict[int, dict] = {}
    for order in order_book or []:
        tag = order.get("OrderUniqueIdentifier")
        if tag:
            order_book_map_by_tag[str(tag)] = order
        oid = order.get("AppOrderID")
        try:
            if oid is not None:
                order_book_map_by_id[int(oid)] = order
        except Exception:
            pass
    
    # Check each SL order for this strategy
    sl_orders = strategy.get("sl_orders", []) or []
    sl_tag_map = strategy.get("sl_tag_map", {}) or {}
    
    for sl_order in sl_orders:
        tag = sl_order.get("tag")
        
        if not tag:
            continue

        # Prefer AppOrderID matching (robust even if broker truncates OrderUniqueIdentifier)
        order_detail = None
        try:
            app_order_id = sl_order.get("app_order_id")
            if app_order_id is not None:
                order_detail = order_book_map_by_id.get(int(app_order_id))
        except Exception:
            order_detail = None
        if order_detail is None:
            order_detail = order_book_map_by_tag.get(str(tag))
        if not order_detail:
            continue
        order_status_raw = order_detail.get("OrderStatus", "")
        order_status = order_status_raw.upper()
        
        # Map SL tag to instrument_id
        instrument_id = sl_tag_map.get(tag)
        if not instrument_id:
            continue
        
        # Find matching position
        matching_position = None
        for pos in strategy.get("positions", []) or []:
            if pos.get("instrument_id") == instrument_id and pos.get("exit_price") is None:
                matching_position = pos
                break
        
        if not matching_position:
            continue
        
        # **MAIN LOGIC: Check XTS order book status**
        if _order_book_status_is_filled(order_status_raw):
            # ✅ SL order was executed - position is CLOSED
            try:
                exit_price = float(order_detail.get("OrderAverageTradedPrice") or 0.0)
                if exit_price <= 0:
                    for alt in ("OrderLastTradedPrice", "LastTradedPrice", "AverageTradedPrice"):
                        v = order_detail.get(alt)
                        if v is not None:
                            try:
                                fv = float(v)
                                if fv > 0:
                                    exit_price = fv
                                    break
                            except (TypeError, ValueError):
                                continue
                if exit_price > 0:
                    matching_position["exit_price"] = exit_price
                    matching_position["exit_time"] = get_ist_timestamp()
                    matching_position["closed_via"] = "SL_FILLED"
                    
                    logger.debug(
                        f"✅ [{strategy['name']}] Position CLOSED via SL: "
                        f"Instrument {instrument_id}, Exit Price: {exit_price}"
                    )
                    # Prefer updating by AppOrderID (stable); tag is retained only for display.
                    try:
                        update_order_status(app_order_id=int(order_detail.get("AppOrderID")), status="Filled", traded_price=exit_price)
                    except Exception:
                        update_order_status(order_tag=tag, status="Filled", traded_price=exit_price)
                    
                    # Log to database
                    if strategy["db_id"] and strategy["db_id"] > 0:
                        try:
                            update_position_exit(
                                strategy["db_id"],
                                instrument_id,
                                exit_price,
                                get_ist_timestamp(),
                            )
                        except Exception as e:
                            logger.error(f"Failed to log position exit: {e}")
                else:
                    logger.warning(
                        "[%s] SL order shows filled-like status but no usable exit price (instrument %s status=%s)",
                        strategy["name"],
                        instrument_id,
                        order_status_raw,
                    )
            except (TypeError, ValueError) as e:
                logger.warning(f"Failed to parse exit price from order {tag}: {e}")
        
        elif order_status in ("REJECTED", "CANCELLED"):
            # ⚠️ SL order failed - position is EXPOSED
            matching_position["sl_status"] = order_status
            logger.warning(
                f"⚠️  [{strategy['name']}] SL order {order_status}: "
                f"Instrument {instrument_id} (position still exposed)"
            )
        
        elif order_status.replace(" ", "").replace("_", "") in (
            "PENDING",
            "OPEN",
            "PARTIALLYFILLED",
            "NEW",
            "REPLACED",
        ):
            # ⏳ SL order still active - position still OPEN
            matching_position["sl_status"] = "WAITING"


def _hint_survivor_sl_to_cost(strategy: dict, msg: str) -> None:
    """Expose why survivor adjust did/didn't run (Overview card)."""
    update_strategy(strategy["name"], survivor_sl_to_cost_hint=msg[:240])


_SURVIVOR_SL_TO_COST_LOG_THROTTLE: Dict[str, float] = {}


def _survivor_sl_to_cost_warn_throttled(
    strategy_name: str, msg: str, interval_sec: float = 90.0
) -> None:
    """Avoid spamming WARNING every MTM tick when survivor adjust is blocked."""
    key = f"{strategy_name}|{msg[:160]}"
    now = time.time()
    if now - _SURVIVOR_SL_TO_COST_LOG_THROTTLE.get(key, 0) < interval_sec:
        return
    _SURVIVOR_SL_TO_COST_LOG_THROTTLE[key] = now
    logger.warning("[%s] Survivor SL-to-cost: %s", strategy_name, msg)


def _broker_modify_order_ok(resp: Any) -> bool:
    """
    True if modify_order returned a successful broker payload (XTS or Kotak).
    None/unknown is treated as OK for backward compatibility (e.g. mocks).
    """
    if resp is None:
        return True
    if isinstance(resp, str):
        return False
    if isinstance(resp, dict):
        if resp.get("result") is not None:
            return True
        if str(resp.get("stat", "")).strip().lower() == "ok":
            return True
        if resp.get("stCode") == 200:
            return True
        if resp.get("Error") or resp.get("Message"):
            return False
        return False
    return True


def _extract_first_float(order: dict, *keys: str) -> Optional[float]:
    """Best-effort float extractor across broker field name variants."""
    for k in keys:
        try:
            v = order.get(k)
            if v is None:
                continue
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _adjust_survivor_sl_to_cost_after_peer_sl(
    client: Any,
    index_config,
    strategy: dict,
    order_book: Optional[List[dict]] = None,
) -> None:
    """
    After one leg of a short straddle is stopped out via SL (closed_via SL_FILLED),
    tighten the surviving leg's stop-limit SL to the original short price (entry/cost).
    """
    if not SURVIVOR_SL_TO_COST_ENABLED:
        return
    if strategy["status"] != "OPEN" or strategy.get("survivor_sl_adjusted_to_cost"):
        return
    if not strategy.get("sl_orders"):
        _hint_survivor_sl_to_cost(strategy, "Waiting: no SL orders in state (need entry SL tags).")
        return
    if client is None:
        _hint_survivor_sl_to_cost(strategy, "No broker client (DEMO?).")
        return

    positions = strategy.get("positions") or []
    if len(positions) != 2:
        _hint_survivor_sl_to_cost(strategy, f"Need exactly 2 legs; have {len(positions)}.")
        return

    closed = [p for p in positions if p.get("exit_price") is not None]
    open_pos = [p for p in positions if p.get("exit_price") is None]
    if len(closed) != 1 or len(open_pos) != 1:
        _hint_survivor_sl_to_cost(
            strategy,
            f"Need 1 closed + 1 open leg (have {len(closed)} closed, {len(open_pos)} open).",
        )
        return
    peer_via = closed[0].get("closed_via")
    if peer_via not in _SURVIVOR_PEER_CLOSED_VIA_OK:
        # Live broker sync/orderbook race can leave `closed_via` unset/non-standard while
        # state still clearly indicates one leg is closed and one survives. Use that state
        # as fallback so survivor SL tighten is not blocked indefinitely.
        _survivor_sl_to_cost_warn_throttled(
            strategy["name"],
            f"fallback: closed_via={peer_via!r}; proceeding because state is 1 closed + 1 open",
            interval_sec=90.0,
        )
        _hint_survivor_sl_to_cost(
            strategy,
            f"Peer closed_via={peer_via!r}; using fallback (1 closed + 1 open) to attempt survivor SL-to-cost.",
        )

    survivor = open_pos[0]
    instrument_id = survivor.get("instrument_id")
    entry_price = survivor.get("entry_price")
    if instrument_id is None or entry_price is None or float(entry_price) <= 0:
        _hint_survivor_sl_to_cost(strategy, "Survivor leg missing instrument_id or entry_price.")
        return

    sl_orders = strategy.get("sl_orders", []) or []
    sl_tag_map = strategy.get("sl_tag_map", {}) or {}
    survivor_sl: Optional[Tuple[int, str]] = None
    for so in sl_orders:
        tag = so.get("tag")
        app_order_id = so.get("app_order_id")
        if not tag or app_order_id is None:
            continue
        if sl_tag_map.get(tag) == instrument_id:
            survivor_sl = (int(app_order_id), str(tag))
            break
    if not survivor_sl:
        _hint_survivor_sl_to_cost(
            strategy,
            "No SL order tag maps to survivor instrument (check sl_tag_map vs survivor leg).",
        )
        return

    app_order_id, tag = survivor_sl

    if order_book is None:
        try:
            order_book = client.get_order_book()
        except Exception as e:
            logger.error(
                "Failed to fetch order book for survivor SL adjust %s: %s",
                strategy["name"],
                e,
            )
            _hint_survivor_sl_to_cost(strategy, f"Order book fetch failed: {e!s}"[:240])
            return

    order_book_map_by_id: Dict[int, dict] = {}
    for order in order_book or []:
        oid = order.get("AppOrderID")
        try:
            if oid is not None:
                order_book_map_by_id[int(oid)] = order
        except Exception:
            pass

    order_detail = order_book_map_by_id.get(app_order_id)
    if not order_detail:
        logger.warning(
            "[%s] Survivor SL-to-cost: SL order not in order book app_order_id=%s",
            strategy["name"],
            app_order_id,
        )
        _hint_survivor_sl_to_cost(
            strategy,
            f"Survivor SL app_order_id={app_order_id} not in order book (sync delay or cancelled).",
        )
        return

    order_status = (order_detail.get("OrderStatus") or "").replace(" ", "").upper()
    _open_sl = {
        "NEW",
        "REPLACED",
        "PENDING",
        "OPEN",
        "PARTIALLYFILLED",
        "PARTIALLY_FILLED",
    }
    if order_status not in _open_sl:
        _hint_survivor_sl_to_cost(
            strategy,
            f"Survivor SL status={order_status!r} — not active; cannot modify.",
        )
        return

    tick = float(index_config.tick_size)
    limit_price = _round_to_tick(float(entry_price), tick)
    trigger = _round_to_tick(max(limit_price - 0.5, 0.05), tick)

    qty = int(order_detail.get("OrderQuantity", 0))
    if qty <= 0:
        qty = int(abs(float(survivor.get("quantity") or 0)))
    if qty <= 0:
        _hint_survivor_sl_to_cost(strategy, "Survivor SL modify skipped: quantity resolved to 0.")
        return
    product_type = (
        order_detail.get("ProductType")
        or getattr(client.interactive, "PRODUCT_MIS", None)
        or "MIS"
    )
    time_in_force = (
        order_detail.get("TimeInForce")
        or getattr(client.interactive, "VALIDITY_DAY", None)
        or "DAY"
    )
    disclosed_qty = int(order_detail.get("OrderDisclosedQuantity", 0) or 0)
    try:
        logger.warning(
            "[%s] Survivor SL-to-cost ATTEMPT: modify_order STOPLIMIT app_order_id=%s tag=%s "
            "product=%s tif=%s limit_price=%.2f stop_price=%.2f qty=%s",
            strategy["name"],
            app_order_id,
            tag,
            product_type,
            time_in_force,
            limit_price,
            trigger,
            qty,
        )
        resp = client.modify_order(
            app_order_id=app_order_id,
            product_type=product_type,
            order_type=client.interactive.ORDER_TYPE_STOPLIMIT,
            quantity=qty,
            disclosed_quantity=disclosed_qty,
            stop_price=float(round(trigger, 2)),
            limit_price=float(round(limit_price, 2)),
            time_in_force=time_in_force,
            tag=tag,
        )
        try:
            resp_str = json.dumps(resp, default=str)[:1500]
        except Exception:
            resp_str = repr(resp)[:1500]
        logger.warning(
            "[%s] Survivor SL-to-cost API response: %s",
            strategy["name"],
            resp_str,
        )
        if not _broker_modify_order_ok(resp):
            logger.error(
                "[%s] Survivor SL-to-cost: broker rejected modify (empty/missing result). "
                "SL not marked tightened; will retry on next MTM.",
                strategy["name"],
            )
            _hint_survivor_sl_to_cost(
                strategy,
                f"modify_order API no result: {resp_str[:200]}",
            )
            return
        # Verify broker-side price update in order book before marking done.
        verified = False
        verify_reason = "unknown"
        for _ in range(2):
            try:
                verify_book = client.get_order_book()
                verify_map: Dict[int, dict] = {}
                for o in verify_book or []:
                    oid = o.get("AppOrderID")
                    try:
                        if oid is not None:
                            verify_map[int(oid)] = o
                    except Exception:
                        continue
                vo = verify_map.get(app_order_id)
                if not vo:
                    verify_reason = f"app_order_id={app_order_id} missing in verify order book"
                    time.sleep(0.25)
                    continue
                v_limit = _extract_first_float(vo, "OrderPrice", "LimitPrice")
                v_stop = _extract_first_float(vo, "OrderStopPrice", "StopPrice", "TriggerPrice")
                if v_limit is None or v_stop is None:
                    verify_reason = "verify order missing limit/stop fields"
                    time.sleep(0.25)
                    continue
                tol = max(tick, 0.05) + 0.01
                if abs(v_limit - limit_price) <= tol and abs(v_stop - trigger) <= tol:
                    verified = True
                    break
                verify_reason = (
                    f"verify mismatch expected(limit={limit_price:.2f},stop={trigger:.2f}) "
                    f"got(limit={v_limit:.2f},stop={v_stop:.2f})"
                )
                time.sleep(0.25)
            except Exception as e:
                verify_reason = f"verify order book fetch failed: {e}"
                time.sleep(0.25)
        if not verified:
            logger.error(
                "[%s] Survivor SL-to-cost: modify accepted but not verified; %s. "
                "Will retry on next MTM.",
                strategy["name"],
                verify_reason,
            )
            _hint_survivor_sl_to_cost(strategy, f"verify failed: {verify_reason}"[:220])
            return
        update_strategy(
            strategy["name"],
            survivor_sl_adjusted_to_cost=True,
            survivor_sl_to_cost_hint="Done: survivor SL tightened to cost.",
        )
        logger.warning(
            "[%s] Survivor SL-to-cost OK: instrument=%s limit=%.2f trigger=%.2f (entry=%.2f)",
            strategy["name"],
            instrument_id,
            limit_price,
            trigger,
            float(entry_price),
        )
    except Exception as e:
        logger.error(
            "[%s] Survivor SL-to-cost: modify_order raised: %s",
            strategy["name"],
            e,
        )
        _hint_survivor_sl_to_cost(strategy, f"modify_order failed: {e!s}"[:220])


def _rebuild_sl_links_from_order_book_for_restored_strategy(
    strategy: dict, order_book: Optional[List[dict]]
) -> bool:
    """
    On restart, DB may restore positions but miss sl_orders/sl_tag_map.
    Rebuild links from broker order book tags like '<strategy>_SL_<instrument>'.
    """
    if not order_book:
        return False
    name = str(strategy.get("name") or "")
    if not name:
        return False
    prefix = f"{name}_SL_"
    open_iids = {
        int(p.get("instrument_id"))
        for p in (strategy.get("positions") or [])
        if p.get("instrument_id") is not None and p.get("exit_price") is None
    }
    closed_iids = {
        int(p.get("instrument_id"))
        for p in (strategy.get("positions") or [])
        if p.get("instrument_id") is not None and p.get("exit_price") is not None
    }
    candidate_iids = open_iids | closed_iids
    if not candidate_iids:
        return False

    sl_orders: List[dict] = []
    sl_tag_map: Dict[str, int] = {}
    seen_tags = set()
    for od in order_book:
        tag = str(od.get("OrderUniqueIdentifier") or "")
        if not tag.startswith(prefix):
            continue
        app_oid = od.get("AppOrderID")
        if app_oid is None:
            continue
        try:
            app_oid_int = int(app_oid)
        except Exception:
            continue
        iid = None
        for k in ("ExchangeInstrumentID", "ExchangeInstrumentId", "InstrumentID", "InstrumentId"):
            v = od.get(k)
            if v is None:
                continue
            try:
                iid = int(v)
                break
            except Exception:
                continue
        if iid is None and tag.count("_") >= 2:
            # Expected format: <strategy>_SL_<instrument>
            try:
                iid = int(tag.rsplit("_", 1)[-1])
            except Exception:
                iid = None
        if iid is None or iid not in candidate_iids:
            continue
        if tag in seen_tags:
            continue
        seen_tags.add(tag)
        sl_orders.append({"app_order_id": app_oid_int, "tag": tag})
        sl_tag_map[tag] = iid

    if not sl_orders:
        return False
    update_strategy(strategy["name"], sl_orders=sl_orders, sl_tag_map=sl_tag_map)
    logger.warning(
        "[%s] Restored SL links from broker order book: %s order(s)",
        strategy["name"],
        len(sl_orders),
    )
    return True


def _check_leg_target_and_close(
    client: Any, index_config, strategy: dict, ltp_map: Dict[int, float]
) -> None:
    """
    If any leg's profit from collected premium reaches LEG_TARGET_PCT (e.g. 65%),
    close that leg by modifying its SL order to marketable LIMIT (single action: leg closed + SL order executed).
    Target is calculated on executed sell order premium (entry_price): profit_pct = (entry_price - ltp) / entry_price * 100.
    """
    if strategy["status"] != "OPEN" or not strategy.get("sl_orders"):
        return
    if client is None:
        return

    sl_orders = strategy.get("sl_orders", []) or []
    sl_tag_map = strategy.get("sl_tag_map", {}) or {}
    # Build instrument_id -> (app_order_id, tag) for SL orders
    instrument_to_sl: Dict[int, Tuple[int, str]] = {}
    for so in sl_orders:
        tag = so.get("tag")
        app_order_id = so.get("app_order_id")
        if not tag or app_order_id is None:
            continue
        instrument_id = sl_tag_map.get(tag)
        if instrument_id is not None:
            instrument_to_sl[int(instrument_id)] = (int(app_order_id), tag)

    target_triggered = set(strategy.get("target_triggered_instruments") or [])  # use set for fast lookup

    try:
        order_book = client.get_order_book()
    except Exception as e:
        logger.error("Failed to fetch order book for leg target check %s: %s", strategy["name"], e)
        return

    order_book_by_id = {}
    order_book_by_tag = {}
    for order in order_book:
        oid = order.get("AppOrderID")
        tag = order.get("OrderUniqueIdentifier")
        if oid is not None:
            order_book_by_id[oid] = order
        if tag:
            order_book_by_tag[tag] = order

    positions = strategy.get("positions") or []
    for pos in positions:
        if pos.get("exit_price") is not None:
            continue
        instrument_id = pos.get("instrument_id")
        entry_price = pos.get("entry_price")
        if instrument_id is None or entry_price is None or float(entry_price) <= 0:
            continue
        instrument_id = int(instrument_id)
        if instrument_id in target_triggered:
            continue

        ltp = ltp_map.get(instrument_id)
        if ltp is None:
            continue
        try:
            entry_f = float(entry_price)
            ltp_f = float(ltp)
        except (TypeError, ValueError):
            continue
        # Short position: profit when LTP drops. Profit % = (entry - ltp) / entry * 100
        profit_pct = (entry_f - ltp_f) / entry_f * 100.0
        if profit_pct < float(LEG_TARGET_PCT):
            continue

        sl_info = instrument_to_sl.get(instrument_id)
        if not sl_info:
            continue
        app_order_id, tag = sl_info
        order_detail = order_book_by_id.get(app_order_id) or order_book_by_tag.get(tag)
        if not order_detail:
            continue
        order_status = (order_detail.get("OrderStatus") or "").replace(" ", "").upper()
        if order_status not in ("NEW", "REPLACED"):
            continue

        order_side = order_detail.get("OrderSide") or client.interactive.TRANSACTION_TYPE_BUY
        limit_px = marketable_limit_price(
            ltp_f,
            str(order_side),
            MARKETABLE_LIMIT_SLIPPAGE_PCT,
            float(index_config.tick_size),
        )
        try:
            client.modify_order(
                app_order_id=app_order_id,
                product_type=order_detail.get("ProductType"),
                order_type=client.interactive.ORDER_TYPE_LIMIT,
                quantity=int(order_detail.get("OrderQuantity", 0)),
                disclosed_quantity=int(order_detail.get("OrderDisclosedQuantity", 0)),
                stop_price=0,
                limit_price=float(limit_px),
                time_in_force=order_detail.get("TimeInForce"),
                tag=tag,
            )
            target_triggered.add(instrument_id)
            update_strategy(strategy["name"], target_triggered_instruments=list(target_triggered))
            logger.debug(
                "✅ [%s] Leg target %.1f%% hit (instrument %s, profit %.1f%%); modified SL to marketable LIMIT (limit=%s)",
                strategy["name"], float(LEG_TARGET_PCT), instrument_id, profit_pct, limit_px,
            )
        except Exception as e:
            logger.error(
                "Failed to modify SL to LIMIT for leg target [%s] instrument %s: %s",
                strategy["name"], instrument_id, e,
            )


def _check_all_positions_closed(strategy: dict) -> bool:
    """
    Determine if ALL positions in strategy are closed.
    Returns True only if all positions have exit_price set (via filled SL orders).
    """
    positions = strategy.get("positions", []) or []
    if not positions:
        return True  # No positions = nothing open
    
    all_closed = all(pos.get("exit_price") is not None for pos in positions)
    return all_closed


def _sync_strategy_positions_from_broker(
    client: Any,
    strategy: dict,
    broker_positions: List[dict],
    ltp_map: dict,
    order_book: Optional[List[dict]] = None,
) -> None:
    """
    Reconcile strategy positions with broker positions.
    
    Important:
    - Broker positions are aggregated. We only force-close a leg in our strategy state if broker net qty is 0
      for that instrument (meaning there is no open position remaining at broker for that instrument).
    - This fixes cases where legs get closed without SL tag becoming FILLED (manual square-off, broker netting,
      target-close order not mapped, etc.).
    """
    instrument_ids = set(strategy.get("instrument_ids", []))
    local_positions = strategy.get("positions") or []
    
    if not instrument_ids or not local_positions:
        return
    
    # Build map of broker positions by instrument_id
    broker_map = {}
    for broker_pos in broker_positions:
        broker_instrument_id = int(broker_pos.get("ExchangeInstrumentId", 0))
        quantity = int(broker_pos.get("Quantity", 0))
        broker_map[broker_instrument_id] = quantity

    # Build a quick map of latest FILLED BUY avg price by instrument from order book (if provided).
    filled_buy_price_by_instrument: Dict[int, float] = {}
    if order_book:
        for o in order_book:
            try:
                status = str(o.get("OrderStatus", "")).upper()
                side = str(o.get("OrderSide", "")).upper()
                if status != "FILLED" or side != "BUY":
                    continue
                inst = o.get("ExchangeInstrumentId", o.get("ExchangeInstrumentID"))
                instrument_id = int(inst) if inst is not None else 0
                if instrument_id == 0:
                    continue
                avg = float(o.get("OrderAverageTradedPrice") or 0.0)
                if avg > 0:
                    filled_buy_price_by_instrument[instrument_id] = avg
            except Exception:
                continue

    # If broker has no net position for an instrument but we still show it open locally, close it.
    db_id = strategy.get("db_id")
    now_ts = get_ist_timestamp()
    sl_orders = strategy.get("sl_orders", []) or []
    sl_tag_map = strategy.get("sl_tag_map", {}) or {}

    for pos in local_positions:
        instrument_id = int(pos.get("instrument_id") or 0)
        if instrument_id == 0:
            continue
        if pos.get("exit_price") is not None:
            continue

        broker_qty = broker_map.get(instrument_id, 0)  # missing => 0 (position fully squared off)
        if broker_qty == 0:
            # Prefer trade/order book executed avg for BUY exits; else LTP; else entry as last resort.
            exit_price = filled_buy_price_by_instrument.get(instrument_id)
            if exit_price is None:
                exit_price = ltp_map.get(instrument_id)
            if exit_price is None:
                try:
                    exit_price = float(pos.get("entry_price") or 0.0)
                except Exception:
                    exit_price = 0.0

            pos["exit_price"] = float(exit_price)
            pos["exit_time"] = now_ts
            pos["closed_via"] = "BROKER_SYNC"

            if db_id and db_id > 0:
                try:
                    update_position_exit(db_id, instrument_id, float(exit_price), now_ts)
                except Exception:
                    logger.exception("Failed to update DB exit for %s instrument %s", strategy.get("name"), instrument_id)

            # If there is still an OPEN/PENDING SL order for this instrument, cancel it to avoid orphan SLs.
            # (We only attempt cancel if we can find the SL tag + app_order_id.)
            sl_tag_for_instrument = None
            for tag, iid in sl_tag_map.items():
                if int(iid) == instrument_id:
                    sl_tag_for_instrument = tag
                    break
            if sl_tag_for_instrument:
                for sl in sl_orders:
                    if sl.get("tag") == sl_tag_for_instrument:
                        app_order_id = sl.get("app_order_id")
                        if client is not None and app_order_id is not None:
                            try:
                                client.cancel_order(int(app_order_id), sl_tag_for_instrument)
                                update_order_status(order_tag=sl_tag_for_instrument, status="CANCELLED")
                            except Exception:
                                logger.exception(
                                    "Failed to cancel stale SL %s for %s",
                                    sl_tag_for_instrument,
                                    strategy.get("name"),
                                )
                        break
    
    # Check if strategy's instruments still have positions on broker
    has_any_position = False
    for instrument_id in instrument_ids:
        broker_qty = broker_map.get(instrument_id, 0)
        if broker_qty != 0:
            has_any_position = True
            break
    
    # If ALL positions for this strategy are squared off on broker, close the strategy
    if not has_any_position and strategy.get("status") == "OPEN":
        update_strategy(
            strategy["name"], 
            status="CLOSED", 
            message="All positions squared off manually",
            positions=[]
        )


def _monitor_mtm(client: Any, index_config, portfolio_sl: float) -> None:
    from state import STATE as _STATE

    expiry = (_STATE.get("index") or {}).get("expiry")
    warm_chain = getattr(client, "warm_option_chain", None)
    if callable(warm_chain) and expiry:
        try:
            warm_chain(index_config, expiry)
        except Exception:
            logger.debug("Option chain warm before MTM failed", exc_info=True)

    positions = client.get_positions()
    try:
        order_book = client.get_order_book()
    except Exception:
        order_book = None
    instruments = [
        {"exchangeSegment": index_config.option_ltp_segment, "exchangeInstrumentID": pos["ExchangeInstrumentId"]}
        for pos in positions
        if int(pos.get("ExchangeInstrumentId") or 0) != 0
    ]
    ltp_map = client.get_ltp_map(instruments)
    for pos in positions:
        iid = int(pos.get("ExchangeInstrumentId") or 0)
        if iid == 0 or iid in ltp_map:
            continue
        get_ltp = getattr(client, "get_option_ltp", None)
        if callable(get_ltp):
            try:
                px = get_ltp(index_config, iid)
                if px is not None:
                    ltp_map[iid] = float(px)
            except Exception:
                logger.debug("MTM LTP fallback failed for %s", iid, exc_info=True)
    limits_mtm = None
    if hasattr(client, "get_portfolio_mtm_from_limits"):
        try:
            limits_mtm = client.get_portfolio_mtm_from_limits()
        except Exception:
            logger.debug("Broker limits MTM unavailable", exc_info=True)
    mtm_source = "kotak_amounts"
    kotak_amt_mtm = calculate_mtm_kotak_amounts(positions, ltp_map)
    if kotak_amt_mtm is not None:
        realized, unrealized, overall = kotak_amt_mtm
    else:
        broker_pos_pnl = calculate_mtm_from_kotak_broker_pnl(positions)
        if broker_pos_pnl is not None:
            mtm_source = "kotak_rpnl_upnl"
            realized, unrealized, overall = broker_pos_pnl
        elif limits_mtm is not None:
            mtm_source = "kotak_limits"
            realized, unrealized, overall = limits_mtm
        else:
            mtm_source = "xts_positions"
            realized, unrealized, overall = calculate_mtm(positions, ltp_map)
    update_portfolio(overall, realized, unrealized, portfolio_sl)

    global _LAST_PORTFOLIO_MTM_LOG
    now_ts = time.time()
    if now_ts - _LAST_PORTFOLIO_MTM_LOG >= 60:
        _LAST_PORTFOLIO_MTM_LOG = now_ts
        breakdown = mtm_position_breakdown(positions, ltp_map)
        logger.info(
            "Portfolio MTM %.2f (realized=%.2f unrealized=%.2f source=%s positions=%d)",
            overall,
            realized,
            unrealized,
            mtm_source,
            len(breakdown),
        )
        for row in breakdown[:12]:
            logger.info(
                "  MTM leg %s id=%s qty=%s ltp=%s booked=%.2f total=%.2f",
                row.get("symbol"),
                row.get("instrument_id"),
                row.get("qty"),
                row.get("ltp"),
                row.get("booked", 0),
                row.get("total_pnl", 0),
            )

    for strategy in STRATEGY_STATE.values():
        # **FIRST**: Sync SL order status from XTS order book (single source of truth)
        _sync_sl_order_status_and_capture_exits(client, strategy, order_book=order_book)

        # **LEG TARGET**: If any leg has reached target % profit, close it by modifying SL to marketable LIMIT
        _check_leg_target_and_close(client, index_config, strategy, ltp_map)
        
        # Sync strategy positions with broker positions to keep state real-time
        _sync_strategy_positions_from_broker(client, strategy, positions, ltp_map, order_book=order_book)

        # **SURVIVOR SL**: After one leg is closed (order-book SL_FILLED or broker sync), tighten survivor SL to cost.
        # Runs after broker sync so BROKER_SYNC closes are visible the same tick.
        _adjust_survivor_sl_to_cost_after_peer_sl(client, index_config, strategy, order_book=order_book)
        
        # Compute strategy MTM from local strategy positions (exit_price -> realized, else LTP -> unrealized)
        # Do this BEFORE we potentially clear positions during closure.
        strategy_positions = strategy.get("positions") or []
        if strategy_positions:
            s_realized, s_unrealized, s_total = calculate_strategy_mtm(strategy_positions, ltp_map)
            update_strategy(strategy["name"], mtm=s_total, realized=s_realized, unrealized=s_unrealized)
            if get_mtm_snapshots_enabled():
                now_ts = time.time()
                if now_ts - _LAST_MTM_LOG.get(strategy["name"], 0) >= 60:
                    log_mtm_snapshot(strategy["name"], s_total, s_realized, s_unrealized)
                    _LAST_MTM_LOG[strategy["name"]] = now_ts
        else:
            # Preserve last known MTM for closed strategies whose positions were cleared.
            s_total = float(strategy.get("mtm", 0.0) or 0.0)
        
        # **CHECK**: If all positions are closed via SL orders, close strategy (logs to trades_closed)
        if strategy["status"] == "OPEN" and _check_all_positions_closed(strategy):
            logger.debug(f"✅ [{strategy['name']}] All positions closed via SL orders - closing strategy")
            _close_strategy(
                client, index_config, strategy, positions,
                "All positions closed via SL orders",
            )

        # Per-strategy SL: only when enabled and strategy_sl > 0.
        strategy_sl_val = strategy.get("strategy_sl")
        if (
            get_trading_flag_or("strategy_sl_enabled", STRATEGY_SL_ENABLED)
            and strategy["status"] == "OPEN"
            and strategy_sl_val is not None
            and float(strategy_sl_val) > 0
            and s_total <= -float(strategy_sl_val)
        ):
            _close_strategy(client, index_config, strategy, positions, "Strategy SL hit")

    if overall <= portfolio_sl:
        # State-only halt: strategies closed below. We intentionally do not schedule.clear() here;
        # the MTM monitor job keeps firing until process exit (see portfolio_sl_note in scheduler diagnostics).
        _square_off_all(client, index_config, positions, "Portfolio SL hit")
        # After squaring off all broker positions, cancel any remaining SL orders
        # and clear them from in-memory state so they can't be reused.
        for strategy in STRATEGY_STATE.values():
            try:
                _cancel_strategy_sl_orders(client, strategy)
            except Exception:
                logger.exception(
                    "Failed to cancel SL orders for strategy %s during portfolio SL handling",
                    strategy.get("name"),
                )

            update_strategy(
                strategy["name"],
                status="CLOSED",
                message="Portfolio SL hit",
                positions=[],
                sl_orders=[],
                sl_tag_map={},
            )


def _update_available_margin(client: Any) -> None:
    if client is None:
        return
    try:
        available_margin = client.get_available_margin()
    except Exception as e:
        if e.__class__.__name__ == "KotakSessionNotReady":
            return
        logger.exception("Failed to fetch available margin")
        return
    update_portfolio_margin(available_margin)
    if available_margin is not None:
        logger.debug("Available margin updated: %s", available_margin)



# Scheduler observability (main thread / schedule library)
_MAIN_LOOP_LAST_TICK: float = 0.0
_JOBS_SCHEDULED_FLAG: bool = False
_SCHEDULER_MINIMAL_MODE: bool = False
_CALM_ZONE_STARTED: bool = False


def _callable_name(fn: Any) -> str:
    if fn is None:
        return "unknown"
    if isinstance(fn, functools.partial):
        inner = fn.func
        return getattr(inner, "__name__", str(inner))
    return getattr(fn, "__name__", str(fn))


def _job_schedule_summary(job: Any) -> str:
    """Human-readable interval for a schedule.Job."""
    unit = getattr(job, "unit", None) or ""
    interval = getattr(job, "interval", None)
    at_time = getattr(job, "at_time", None)
    if at_time is not None:
        return f"daily at {at_time}"
    if interval is not None and unit:
        return f"every {interval} {unit}"
    return str(unit or "scheduled")


def _job_display_label(job: Any) -> str:
    fn = getattr(job, "job_func", None)
    name = _callable_name(fn)
    titles = {
        "_monitor_mtm": "Monitor MTM & sync (includes survivor SL-to-cost)",
        "_execute_strategy": "Execute strategy",
        "_update_available_margin": "Update available margin",
        "cleanup_old_data": "Daily DB cleanup (midnight IST)",
    }
    base = titles.get(name, name)
    if isinstance(fn, functools.partial) and name == "_execute_strategy":
        strat = (fn.keywords or {}).get("strategy") or {}
        sn = strat.get("name")
        if sn:
            return f"{base}: {sn}"
    return base


def _serialize_job(job: Any, index: int) -> Dict[str, Any]:
    nrun = getattr(job, "next_run", None)
    lrun = getattr(job, "last_run", None)
    should_run_val: Optional[bool] = None
    try:
        sr = getattr(job, "should_run", None)
        if callable(sr):
            should_run_val = bool(sr())
    except Exception:
        should_run_val = None
    return {
        "id": index,
        "function": _callable_name(getattr(job, "job_func", None)),
        "label": _job_display_label(job),
        "schedule": _job_schedule_summary(job),
        "next_run": nrun.isoformat() if nrun is not None and hasattr(nrun, "isoformat") else None,
        "last_run": lrun.isoformat() if lrun is not None and hasattr(lrun, "isoformat") else None,
        "should_run": should_run_val,
    }


def get_scheduler_diagnostics() -> Dict[str, Any]:
    """
    Snapshot for UI: schedule jobs, main-loop heartbeat, flags.
    Safe to call from Flask thread (read-only on schedule.jobs).
    """
    now = time.time()
    tick = _MAIN_LOOP_LAST_TICK
    age = None if tick <= 0 else max(0.0, now - tick)
    jobs_raw = list(schedule.jobs)
    jobs = [_serialize_job(j, i) for i, j in enumerate(jobs_raw)]
    return {
        "main_loop_last_tick_unix": tick if tick > 0 else None,
        "heartbeat_age_sec": age,
        "jobs_scheduled_at_startup": _JOBS_SCHEDULED_FLAG,
        "minimal_schedule_non_expiry": _SCHEDULER_MINIMAL_MODE,
        "job_count": len(jobs),
        "jobs": jobs,
        "survivor_note": (
            "Survivor SL-to-cost runs inside the _monitor_mtm job (every 3s when full schedule is active), "
            "not as a separate named job."
        ),
        "portfolio_sl_note": (
            "Portfolio SL closes strategies in state but does not clear the Python schedule; "
            "the monitor job may still run until process exit."
        ),
    }


def register_scheduler_snapshot_with_state() -> None:
    """Wire diagnostics into /state (call after _schedule_jobs)."""
    from state import set_scheduler_snapshot_fn

    set_scheduler_snapshot_fn(get_scheduler_diagnostics)


def _schedule_jobs(client: Any, index_config, expiry: str) -> None:
    global _SCHEDULER_MINIMAL_MODE, _JOBS_SCHEDULED_FLAG

    if not _is_expiry_day(expiry) and not get_trading_flag_or("trade_non_expiry_day", TRADE_NON_EXPIRY_DAY):
        logger.debug("Non-expiry day and TRADE_NON_EXPIRY_DAY is disabled — skipping all strategy scheduling")
        _SCHEDULER_MINIMAL_MODE = True
        for strategy in STRATEGY_STATE.values():
            update_strategy(strategy["name"], status="DISABLED", message="No trading on non-expiry day")
        schedule.every(60).seconds.do(_update_available_margin, client=client)
        schedule.every().day.at("00:00").do(cleanup_old_data)
        _JOBS_SCHEDULED_FLAG = True
        register_scheduler_snapshot_with_state()
        _update_available_margin(client)
        return

    _SCHEDULER_MINIMAL_MODE = False
    test_mode = os.getenv("TEST_FIRST_STRATEGY_IN_1MIN", "false").lower() == "true"
    
    for idx, strategy in enumerate(STRATEGY_STATE.values()):
        if idx == 0 and test_mode:
            logger.debug("TEST MODE: First strategy in 1 minute")
            schedule.every(1).minutes.do(
                _execute_strategy,
                client=client,
                index_config=index_config,
                expiry=expiry,
                strategy=strategy,
                force=True,
            )
        else:
            schedule.every().day.at(strategy["time"]).do(
                _execute_strategy, client=client, index_config=index_config, expiry=expiry, strategy=strategy
            )

    schedule.every(3).seconds.do(_monitor_mtm, client=client, index_config=index_config, portfolio_sl=PORTFOLIO_SL_LIMIT)
    schedule.every(max(1, int(CALM_ZONE_GATEKEEPER_POLL_SECONDS))).seconds.do(
        _process_waiting_for_calm, client=client, index_config=index_config, expiry=expiry
    )
    schedule.every(60).seconds.do(_update_available_margin, client=client)
    schedule.every().day.at("00:00").do(cleanup_old_data)
    _JOBS_SCHEDULED_FLAG = True
    register_scheduler_snapshot_with_state()
    _update_available_margin(client)


def _kotak_session_ready(client: Any) -> bool:
    from config import BROKER_BACKEND

    if BROKER_BACKEND != "kotak":
        return True
    try:
        if hasattr(client, "is_session_active"):
            return bool(client.is_session_active())
        import kotak_auth

        return kotak_auth.client_session_active()
    except Exception:
        return False


def _start_calm_zone_monitor_once(client: Any) -> None:
    global _CALM_ZONE_STARTED
    if _CALM_ZONE_STARTED or client is None:
        return
    if not _kotak_session_ready(client):
        logger.debug("Calm zone monitor deferred until Kotak TOTP login completes")
        return
    from calm_zone_service import start_calm_zone_monitor_thread

    start_calm_zone_monitor_thread(client)
    _CALM_ZONE_STARTED = True


def _complete_kotak_bootstrap() -> None:
    """After dashboard TOTP login: pick expiry, schedule jobs, start calm-zone thread."""
    client = None
    try:
        import kotak_auth

        client = kotak_auth.get_client()
    except Exception:
        pass
    if client is None:
        logger.error("Kotak bootstrap: trading client not registered")
        return
    try:
        index_config, expiry = _pick_index_and_expiry(client)
        set_index(index_config.name, expiry)
        _load_strategy_state(client, index_config)
        _wait_for_kotak_trading_session(client)
        if not _JOBS_SCHEDULED_FLAG:
            _schedule_jobs(client, index_config, expiry)

        def _run_catch_up() -> None:
            try:
                _catch_up_missed_scheduled_strategies(client, index_config, expiry)
            except Exception:
                logger.exception("Catch-up thread failed")

        threading.Thread(target=_run_catch_up, daemon=True, name="kotak-catch-up").start()
        _start_calm_zone_monitor_once(client)
        logger.info("Kotak bootstrap complete: %s %s", index_config.name, expiry)
    except RuntimeError as e:
        logger.error("Kotak bootstrap failed (expiry/index): %s", e)
        set_index_error(str(e))
        from threading import Thread

        Thread(target=lambda: _retry_pick_expiry(client, {}), daemon=True).start()


def _retry_pick_expiry(client: Any, auth: dict) -> None:
    """Periodically retry picking expiry if initial attempt failed."""
    global STRATEGY_STATE
    retry_interval = 10  # Start with 10 seconds
    max_interval = 60    # Max out at 60 seconds
    attempt = 0
    
    while True:
        time.sleep(retry_interval)
        attempt += 1
        
        try:
            logger.debug(f"🔄 Retry {attempt}: Checking for expiry data...")
            index_config, expiry = _pick_index_and_expiry(client)
            
            logger.debug(f"✓ Expiry found! Index: {index_config.name} | Expiry: {expiry}")
            set_index(index_config.name, expiry)
            _load_strategy_state(client, index_config)

            _wait_for_kotak_trading_session(client)
            if not _JOBS_SCHEDULED_FLAG:
                _schedule_jobs(client, index_config, expiry)

            def _run_catch_up() -> None:
                try:
                    _catch_up_missed_scheduled_strategies(client, index_config, expiry)
                except Exception:
                    logger.exception("Catch-up thread failed")

            threading.Thread(target=_run_catch_up, daemon=True, name="kotak-catch-up").start()

            _start_calm_zone_monitor_once(client)
            logger.debug("✓ Bot is now operational")
            return  # Success, exit retry loop
            
        except RuntimeError as e:
            # Update error message on UI
            error_msg = f"Still waiting for expiry data... (Attempt {attempt})"
            set_index_error(error_msg)
            logger.debug(f"Retry {attempt} failed: {e}")
            
            # Increase retry interval, but cap at max
            retry_interval = min(retry_interval + 5, max_interval)
            current_time = get_ist_now().strftime("%H:%M:%S")
            logger.debug(f"⏳ Retrying in {retry_interval}s... Current time: {current_time} (Market hours: 09:15-15:30)")


def main() -> None:
    init_journal()
    init_db()
    if DEMO_MODE:
        logger.debug("DEMO MODE - Simulated data")
        client = None
        index_config = INDEX_CONFIGS["NIFTY"]
        expiry = "08FEB2026"
        auth = {"username": "admin", "password": "admin123"}
    else:
        from config import BROKER_BACKEND, load_kotak_credentials
        import kotak_auth

        creds = load_login_credentials()
        auth = get_basic_auth_creds(creds)
        client = create_trading_client()
        kotak_cfg = load_kotak_credentials() if BROKER_BACKEND == "kotak" else {}
        kotak_auth.init_kotak_auth_mode(has_auto_totp_secret=bool(kotak_cfg.get("totp_secret")))

        index_config = None
        expiry = None
        from db import is_kotak_totp_satisfied_today, mark_kotak_totp_satisfied_today

        defer_kotak_login = (
            kotak_auth.kotak_ui_totp_enabled() and not kotak_auth.client_session_active()
        )

        if defer_kotak_login:
            kotak_auth.register_pending_client(client, _complete_kotak_bootstrap)
            logger.info(
                "Kotak: open the dashboard and enter today's TOTP (once per day, IST)."
            )
        else:
            client.login()
            if BROKER_BACKEND == "kotak":
                mark_kotak_totp_satisfied_today()
            try:
                index_config, expiry = _pick_index_and_expiry(client)
            except RuntimeError as e:
                logger.error(f"Failed to pick index and expiry: {e}")
                index_config = None
                expiry = None

    logger.debug("Index: %s | Expiry: %s", index_config.name if index_config else "NOT SET", expiry if expiry else "NOT SET")
    # Clean up all previous day data on startup
    cleanup_previous_day_data()

    # Build today's strategy plan (per index) and STRATEGY_STATE
    global _MAIN_LOOP_LAST_TICK
    STRATEGY_STATE.clear()
    if index_config is not None:
        _load_strategy_state(client, index_config)

    init_state(STRATEGY_STATE)
    init_trading_flags(USE_PREMIUM_BASED_STRIKE, STRATEGY_SL_ENABLED, TRADE_NON_EXPIRY_DAY)
    set_bot_runtime_flags(survivor_sl_to_cost_enabled=bool(SURVIVOR_SL_TO_COST_ENABLED))
    set_mtm_snapshots_enabled(DB_ENABLE_MTM_SNAPSHOTS)
    if index_config is not None:
        set_index(index_config.name, expiry)
    else:
        set_index_error("No expiries found for NIFTY or SENSEX. Please check market hours (09:15-15:30).")

    app = create_app(auth["username"], auth["password"])
    
    # Only schedule jobs if we have valid index and expiry
    jobs_scheduled = False
    if not DEMO_MODE and index_config is not None:
        _wait_for_kotak_trading_session(client)
        _schedule_jobs(client, index_config, expiry)

        def _run_catch_up() -> None:
            try:
                _catch_up_missed_scheduled_strategies(client, index_config, expiry)
            except Exception:
                logger.exception("Catch-up thread failed")

        threading.Thread(target=_run_catch_up, daemon=True, name="kotak-catch-up").start()
        jobs_scheduled = True
    elif not DEMO_MODE:
        register_scheduler_snapshot_with_state()
        if client is not None and _kotak_session_ready(client):
            schedule.every(60).seconds.do(_update_available_margin, client=client)
            _update_available_margin(client)

    from threading import Thread

    def _parse_web_ui_port() -> int:
        """Default 80 (EC2 setup). Ignore empty/invalid env values."""
        raw = (os.getenv("WEB_UI_PORT") or "80").strip()
        try:
            p = int(raw)
        except ValueError:
            logger.warning("WEB_UI_PORT=%r invalid; using 80", os.getenv("WEB_UI_PORT"))
            return 80
        if not (1 <= p <= 65535):
            logger.warning("WEB_UI_PORT=%s out of range; using 80", p)
            return 80
        return p

    ui_port = _parse_web_ui_port()

    def _run_flask_server() -> None:
        try:
            ensure_http_access_not_logged()
            app.run(
                host="0.0.0.0",
                port=ui_port,
                debug=False,
                use_reloader=False,
                threaded=True,
            )
        except OSError as e:
            logger.error(
                "Flask UI failed to bind port %s (Permission denied on ports <1024 without root, "
                "or address already in use): %s",
                ui_port,
                e,
            )
        except Exception as e:
            logger.exception("Flask UI thread crashed: %s", e)

    ui_thread = Thread(target=_run_flask_server)
    ui_thread.daemon = True
    ui_thread.start()

    if not DEMO_MODE and client is not None and _kotak_session_ready(client):
        _start_calm_zone_monitor_once(client)

    logger.info(
        "UI dashboard at http://0.0.0.0:%s (path /dashboard) | basic auth user: %s",
        ui_port,
        auth["username"],
    )
    
    # If no expiry was found, retry periodically
    if index_config is None and not DEMO_MODE:
        logger.debug("⏳ Waiting for expiry data... Current time: %s (Market hours: 09:15-15:30)", get_ist_now().strftime("%H:%M:%S"))
        retry_thread = Thread(target=lambda: _retry_pick_expiry(client, auth), daemon=True)
        retry_thread.start()

    if DEMO_MODE:
        update_portfolio(2500.50, 1200.25, 1300.25, PORTFOLIO_SL_LIMIT)
        update_portfolio_margin(250000.0)
        for idx, strategy in enumerate(STRATEGY_STATE.values()):
            update_strategy(
                strategy["name"],
                status="IDLE",
                strike=19850 + (idx * 100),
                mtm=500.0 - (idx * 200),
                realized=100.0,
                unrealized=400.0 - (idx * 200),
            )
        register_scheduler_snapshot_with_state()
        while True:
            _MAIN_LOOP_LAST_TICK = time.time()
            time.sleep(1)
    else:
        _MAIN_LOOP_LAST_TICK = time.time()
        while True:
            _MAIN_LOOP_LAST_TICK = time.time()
            schedule.run_pending()
            time.sleep(1)


if __name__ == "__main__":
    main()
