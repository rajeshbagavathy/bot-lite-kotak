import datetime
import logging
import math
import os
import time
from typing import Dict, List, Optional, Tuple

import schedule

from config import (
    ACC_NAME,
    INDEX_CONFIGS,
    HEDGE_LOTS,
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
    PORTFOLIO_SL_LIMIT,
    REQUIRED_MARGIN_PER_STRATEGY,
    SOURCE,
    STRATEGIES,
    STRATEGY_LOTS_NON_EXPIRY,
    TRADE_NON_EXPIRY_DAY,
    DEMO_MODE,
    DB_ENABLE_MTM_SNAPSHOTS,
    get_basic_auth_creds,
    load_credentials,
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
    restore_todays_strategies,
    get_ist_timestamp,
    get_ist_now,
)
from mtm import calculate_mtm, calculate_strategy_mtm
from state import (
    init_state,
    set_index,
    set_index_error,
    set_spot,
    update_portfolio,
    update_portfolio_margin,
    update_strategy,
    get_mtm_snapshots_enabled,
    set_mtm_snapshots_enabled,
)
from ui import create_app
from xts_client import XTSClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")],
)
logger = logging.getLogger("xts-bot-lite")
APP_START_TIME = get_ist_now()
_LAST_MTM_LOG: Dict[str, float] = {}  # strategy_name -> last log timestamp (for throttling)


def _pick_index_and_expiry(client: XTSClient) -> Tuple[dict, str]:
    expiry_map = {}
    for config in INDEX_CONFIGS.values():
        expiries = client.get_expiry_dates(config)
        if expiries:
            expiry_map[config.name] = expiries[0]
            logger.info(f"  {config.name} expiry: {expiries[0]}")

    if not expiry_map:
        logger.error("No expiries found for any index")
        raise RuntimeError("No expiries found for NIFTY or SENSEX")

    earliest = min(expiry_map.values())
    candidates = [name for name, date in expiry_map.items() if date == earliest]
    chosen_name = "SENSEX" if "SENSEX" in candidates else candidates[0]
    expiry = client.format_expiry_for_options(earliest)
    logger.info(f"Selected: {chosen_name} expiry: {expiry}")
    return INDEX_CONFIGS[chosen_name], expiry


def _get_atm_strike(client: XTSClient, index_config) -> Optional[int]:
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
    client: XTSClient,
    index_config,
    expiry: str,
    option_type: str,
    atm_strike: int,
    target_premium: float,
    min_premium: float,
    max_premium: float,
    max_steps: int = 40,
) -> Optional[dict]:
    """
    Find a far-OTM hedge option with LTP within [min_premium, max_premium], closest to target_premium.
    For CE: scans strikes above ATM; For PE: scans strikes below ATM.
    Returns dict with strike, instrument_id, ltp.
    """
    direction = 1 if option_type.upper() == "CE" else -1
    strike_diff = int(index_config.strike_diff)

    candidates: List[Tuple[int, int]] = []
    for i in range(1, max_steps + 1):
        strike = atm_strike + (i * strike_diff * direction)
        instrument_id = client.get_option_instrument_id(index_config, expiry, option_type.upper(), strike)
        if instrument_id:
            try:
                candidates.append((strike, int(instrument_id)))
            except (TypeError, ValueError):
                continue

    if not candidates:
        return None

    instruments = [
        {"exchangeSegment": index_config.option_ltp_segment, "exchangeInstrumentID": instrument_id}
        for _, instrument_id in candidates
    ]
    ltp_map = client.get_ltp_map(instruments)

    best = None  # (abs_diff, strike, instrument_id, ltp)
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
        diff = abs(ltp_val - float(target_premium))
        if best is None or diff < best[0]:
            best = (diff, strike, instrument_id, ltp_val)

    if best is None:
        return None

    _, strike, instrument_id, ltp_val = best
    return {"strike": strike, "instrument_id": instrument_id, "ltp": ltp_val}


def _ensure_margin_or_skip_strategy(
    client: XTSClient,
    index_config,
    expiry: str,
    strategy: dict,
    atm_strike: int,
) -> bool:
    """
    Ensure required margin is available before placing the straddle.
    If margin is low, buys far-OTM hedges (CE above ATM, PE below ATM), refreshes margin,
    and only proceeds if margin becomes sufficient.
    If still insufficient, closes hedges and marks strategy as ERROR.
    """
    name = strategy["name"]

    available = client.get_available_margin()
    update_portfolio_margin(available)
    if available is not None and float(available) >= float(REQUIRED_MARGIN_PER_STRATEGY):
        return True

    target_premium = float(HEDGE_TARGET_PREMIUM_EXPIRY) if _is_expiry_day(expiry) else float(HEDGE_TARGET_PREMIUM_NON_EXPIRY)
    min_premium = float(HEDGE_PREMIUM_MIN_EXPIRY) if _is_expiry_day(expiry) else float(HEDGE_PREMIUM_MIN_NON_EXPIRY)
    max_premium = float(HEDGE_PREMIUM_MAX_EXPIRY) if _is_expiry_day(expiry) else float(HEDGE_PREMIUM_MAX_NON_EXPIRY)

    update_strategy(
        name,
        message=f"Low margin ({available}); buying hedges targeting ~₹{target_premium} (LTP in ₹{min_premium}-₹{max_premium})",
    )

    pe_hedge = _find_hedge_by_target_premium(
        client=client,
        index_config=index_config,
        expiry=expiry,
        option_type="PE",
        atm_strike=atm_strike,
        target_premium=target_premium,
        min_premium=min_premium,
        max_premium=max_premium,
    )
    ce_hedge = _find_hedge_by_target_premium(
        client=client,
        index_config=index_config,
        expiry=expiry,
        option_type="CE",
        atm_strike=atm_strike,
        target_premium=target_premium,
        min_premium=min_premium,
        max_premium=max_premium,
    )

    if not pe_hedge or not ce_hedge:
        update_strategy(name, status="ERROR", message="Margin low; unable to find hedge options")
        return False

    hedge_qty = int(HEDGE_LOTS) * int(index_config.lot_size)
    hedge_orders = []
    for hedge, side in ((pe_hedge, "PE"), (ce_hedge, "CE")):
        tag = f"{name}_HEDGE_{side}_BUY_{int(time.time())}"
        oid = client.place_market_order(
            index_config=index_config,
            instrument_id=int(hedge["instrument_id"]),
            order_side=client.interactive.TRANSACTION_TYPE_BUY,
            quantity=hedge_qty,
            tag=tag,
            product_type=client.interactive.PRODUCT_MIS,
        )
        if oid:
            hedge_orders.append({"app_order_id": oid, "tag": tag, "instrument_id": int(hedge["instrument_id"])})
        else:
            # Rollback any hedge order already placed
            for placed in hedge_orders:
                try:
                    client.cancel_order(placed["app_order_id"], placed["tag"])
                except Exception:
                    logger.exception("Failed to cancel hedge order %s", placed)
            # If anything got filled, close using broker positions
            try:
                positions = client.get_positions()
                _close_positions_for_instruments(
                    client,
                    index_config,
                    positions,
                    [p["instrument_id"] for p in hedge_orders],
                )
            except Exception:
                logger.exception("Failed to rollback hedge positions for %s", name)

            update_strategy(name, status="ERROR", message="Margin low; hedge order placement failed")
            return False

    update_strategy(
        name,
        hedge_orders=hedge_orders,
        hedge_target_premium=target_premium,
        hedge_qty=hedge_qty,
        hedge_strikes={"PE": pe_hedge.get("strike"), "CE": ce_hedge.get("strike")},
    )

    time.sleep(5)
    available2 = client.get_available_margin()
    update_portfolio_margin(available2)
    if available2 is not None and float(available2) >= float(REQUIRED_MARGIN_PER_STRATEGY):
        update_strategy(name, message=f"Margin improved ({available2}); proceeding with straddle")
        return True

    # Still insufficient: close hedge positions and skip strategy
    try:
        positions = client.get_positions()
        _close_positions_for_instruments(
            client,
            index_config,
            positions,
            [int(pe_hedge["instrument_id"]), int(ce_hedge["instrument_id"])],
        )
    except Exception:
        logger.exception("Failed to close hedge positions for %s", name)

    update_strategy(name, status="ERROR", message="Margin not available even after hedges")
    return False


def _round_to_tick(price: float, tick_size: float = 0.05) -> float:
    """Round price to nearest tick size (required by XTS API)."""
    return math.ceil(price / tick_size) * tick_size


def _place_leg_sl_orders(
    client: XTSClient,
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
        
        tag = f"{strategy_name}_SL_{order['TradingSymbol']}_{int(time.time())}"
        instrument_id = int(order["ExchangeInstrumentID"])
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
                tag,
                instrument_id,
                order.get("TradingSymbol", ""),
                quantity,
                "STOPLIMIT",
                "BUY",
            )
    return sl_orders, tag_to_instrument


def _get_filled_orders(order_book: List[dict], tags: List[str]) -> List[dict]:
    return [
        order
        for order in order_book
        if order.get("OrderUniqueIdentifier") in tags
        and str(order.get("OrderStatus", "")).replace(" ", "").upper()
        in ("FILLED", "PARTIALLYFILLED")
        and float(order.get("OrderAverageTradedPrice") or 0) > 0
    ]


def _execute_strategy(client: XTSClient, index_config, expiry: str, strategy, force: bool = False) -> None:
    if strategy["status"] not in ("PENDING", "ERROR"):
        return
    if not force and get_ist_now().strftime("%H:%M:%S") < strategy["time"]:
        return

    name = strategy["name"]
    is_expiry = _is_expiry_day(expiry)
    atm_strike = _get_atm_strike(client, index_config)
    if atm_strike is None:
        update_strategy(name, status="ERROR", message="Spot LTP unavailable")
        return

    # Expiry: ITM (CE below spot, PE above spot). Non-expiry: ATM straddle (same strike CE & PE).
    strike_diff = int(index_config.strike_diff)
    if is_expiry:
        n = int(ITM_STRIKES_SENSEX) if index_config.name == "SENSEX" else int(ITM_STRIKES_NIFTY)
        ce_strike = atm_strike - n * strike_diff
        pe_strike = atm_strike + n * strike_diff
    else:
        ce_strike = pe_strike = atm_strike

    ce_id = client.get_option_instrument_id(index_config, expiry, "CE", ce_strike)
    pe_id = client.get_option_instrument_id(index_config, expiry, "PE", pe_strike)
    if not ce_id or not pe_id:
        update_strategy(name, status="ERROR", message="Option instruments not found")
        return

    # Pre-check margin; if low, buy far-OTM hedges first and refresh.
    if not _ensure_margin_or_skip_strategy(client, index_config, expiry, strategy, atm_strike):
        return

    effective_lots = int(strategy["lots"])
    effective_leg_sl_pct = float(strategy["leg_sl_pct"])
    qty = effective_lots * index_config.lot_size
    ce_tag = f"{name}_CE_SELL_{int(time.time())}"
    pe_tag = f"{name}_PE_SELL_{int(time.time())}"

    placed_entry = []
    for instrument_id, tag in [(ce_id, ce_tag), (pe_id, pe_tag)]:
        order_id = client.place_market_order(
            index_config=index_config,
            instrument_id=instrument_id,
            order_side=client.interactive.TRANSACTION_TYPE_SELL,
            quantity=qty,
            tag=tag,
            product_type=client.interactive.PRODUCT_MIS,
        )
        if order_id:
            placed_entry.append({"app_order_id": order_id, "tag": tag, "instrument_id": int(instrument_id)})
        else:
            # Rollback any already placed entry order to avoid a naked leg.
            for placed in placed_entry:
                try:
                    client.cancel_order(placed["app_order_id"], placed["tag"])
                except Exception:
                    logger.exception("Failed to cancel entry order %s", placed)

            try:
                positions = client.get_positions()
                _close_positions_for_instruments(
                    client,
                    index_config,
                    positions,
                    [p["instrument_id"] for p in placed_entry],
                )
            except Exception:
                logger.exception("Failed to rollback partial straddle for %s", name)

            update_strategy(name, status="ERROR", message="Entry order placement failed (margin/blocked)")
            return

    for instrument_id, tag in [(ce_id, ce_tag), (pe_id, pe_tag)]:
        log_order(name, tag, int(instrument_id), "", qty, "MARKET", "SELL")

    update_strategy(
        name,
        status="OPEN",
        strike=atm_strike,
        strike_ce=ce_strike,
        strike_pe=pe_strike,
        instrument_ids=[ce_id, pe_id],
        entry_time=get_ist_timestamp(),
        order_tags=[ce_tag, pe_tag],
    )

    # Log strategy execution to database
    strategy_db_id = log_strategy_execution(
        name,
        atm_strike,
        get_ist_timestamp(),
        effective_lots,
        effective_leg_sl_pct,
        strategy.get("strategy_sl", 0.0),
    )
    update_strategy(name, db_id=strategy_db_id)

    time.sleep(5)
    # Wait for entry orders to get filled (or partially filled) before placing SLs.
    # This loop is resilient to slower fills and avoids missing SL placement.
    filled: List[dict] = []
    tags = [ce_tag, pe_tag]
    max_wait_seconds = 45
    poll_interval = 3
    max_attempts = max(1, int(max_wait_seconds // poll_interval))
    attempts = 0

    while True:
        try:
            order_book = client.get_order_book()
        except Exception as e:
            logger.error("Failed to fetch order book while waiting for fills: %s", e)
            break

        filled = _get_filled_orders(order_book, tags)
        if len(filled) >= 2:
            for order in filled:
                tag_id = order.get("OrderUniqueIdentifier")
                price = order.get("OrderAverageTradedPrice")
                if tag_id:
                    update_order_status(tag_id, "Filled", float(price) if price is not None else None)
            break

        attempts += 1
        if attempts >= max_attempts:
            break

        time.sleep(poll_interval)

    if not filled:
        logger.warning(
            "No filled entry orders found for strategy %s (tags=%s); "
            "SKIPPING SL placement for now.",
            name,
            tags,
        )
        # Still update positions/DB if any eventually appear via other flows,
        # but we can't place SLs safely without fills.
        return

    sl_orders, tag_to_instrument = _place_leg_sl_orders(client, index_config, filled, effective_leg_sl_pct, name)
    positions = []
    for order in filled:
        try:
            instrument_id = int(order.get("ExchangeInstrumentID", 0))
            quantity = -abs(int(order.get("OrderQuantity", 0)))
            entry_price = float(order.get("OrderAverageTradedPrice", 0.0))
        except (TypeError, ValueError):
            continue
        # Target price: close leg when LTP reaches this (65% profit on executed sell premium = entry_price)
        target_price = round(entry_price * (1 - LEG_TARGET_PCT / 100.0), 2)
        positions.append(
            {
                "instrument_id": instrument_id,
                "quantity": quantity,
                "entry_price": entry_price,
                "target_price": target_price,
                "exit_price": None,
                "symbol": order.get("TradingSymbol"),
            }
        )
        # Log position to database
        if strategy["db_id"] and strategy["db_id"] > 0:
            log_position(
                strategy["db_id"],
                order.get("TradingSymbol"),
                instrument_id,
                quantity,
                entry_price,
                get_ist_timestamp(),
            )
    update_strategy(name, sl_orders=sl_orders, positions=positions, sl_tag_map=tag_to_instrument)


def _place_close_order(client: XTSClient, index_config, pos: dict, tag_prefix: str) -> None:
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
    client: XTSClient, index_config, positions: List[dict], instrument_ids: List[int]
) -> None:
    for pos in positions:
        if int(pos["ExchangeInstrumentId"]) in instrument_ids:
            _place_close_order(client, index_config, pos, "CLOSE")


def _cancel_strategy_sl_orders(client: XTSClient, strategy: dict) -> None:
    for sl_order in strategy.get("sl_orders", []) or []:
        try:
            client.cancel_order(sl_order["app_order_id"], sl_order["tag"])
        except Exception:
            logger.exception("Failed to cancel SL order %s", sl_order)


def _close_strategy_via_open_sl_orders(client: XTSClient, strategy: dict) -> None:
    """
    Close strategy by converting open SL orders to market execution.
    
    This approach:
    1. Checks order book status of each SL order
    2. If status = 'New'/'Replaced' → SL still open, modify to market execution
    3. If status = 'Filled' → Already closed by individual leg SL, skip
    4. If status = 'Cancelled'/'Rejected' → Failed, skip
    
    Benefit: Only closes still-open legs, avoids double-closing already-filled positions.
    This handles scenarios where one leg SL hit before strategy SL.
    """
    sl_orders = strategy.get("sl_orders", []) or []
    if not sl_orders:
        logger.info(f"No SL orders found for strategy {strategy['name']}")
        return
    
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
    
    # Process each SL order
    for sl_order in sl_orders:
        app_order_id = sl_order.get("app_order_id")
        tag = sl_order.get("tag")
        
        # Find this SL order in the order book
        order_detail = order_book_map.get(app_order_id) or order_book_map.get(tag)
        
        if not order_detail:
            logger.warning(
                f"SL order not found in order book: {strategy['name']} - {tag}"
            )
            continue
        
        order_status = order_detail.get("OrderStatus", "").upper()
        
        # Check if SL order is still open
        if order_status in ("NEW", "REPLACED"):
            # ✅ SL is still open - modify to market execution
            try:
                client.modify_order(
                    app_order_id=app_order_id,
                    product_type=order_detail.get("ProductType"),
                    order_type=client.interactive.ORDER_TYPE_MARKET,
                    quantity=int(order_detail.get("OrderQuantity", 0)),
                    disclosed_quantity=int(order_detail.get("OrderDisclosedQuantity", 0)),
                    stop_price=0,  # Market execution
                    limit_price=0,  # Market execution
                    time_in_force=order_detail.get("TimeInForce"),
                    tag=tag,
                )
                logger.info(
                    f"✅ [{strategy['name']}] Modified SL order to market: {tag} "
                    f"(Qty: {order_detail.get('OrderQuantity')})"
                )
            except Exception as e:
                logger.error(
                    f"Failed to modify SL order for {strategy['name']}: {tag} - {e}"
                )
        
        elif order_status == "FILLED":
            # ℹ️ SL already executed - position already closed
            logger.info(
                f"ℹ️  [{strategy['name']}] SL already FILLED, skipping: {tag}"
            )
        
        elif order_status in ("CANCELLED", "REJECTED"):
            # ⚠️ SL order failed - position is exposed
            logger.warning(
                f"⚠️  [{strategy['name']}] SL order {order_status}: {tag} "
                f"(position exposed, manual intervention may be needed)"
            )


def _close_strategy(client: XTSClient, index_config, strategy: dict, positions: List[dict], reason: str) -> None:
    if strategy["status"] in ("CLOSED", "CLOSING"):
        return
    update_strategy(strategy["name"], status="CLOSING", message=reason)
    _close_strategy_via_open_sl_orders(client, strategy)
    
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


def _square_off_all(client: XTSClient, index_config, positions: List[dict], reason: str) -> None:
    logger.warning("Square-off all positions: %s", reason)
    for pos in positions:
        _place_close_order(client, index_config, pos, "SQUAREOFF")


def _sync_sl_order_status_and_capture_exits(client: XTSClient, strategy: dict) -> None:
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
    
    try:
        order_book = client.get_order_book()
    except Exception as e:
        logger.error(f"Failed to fetch order book for {strategy['name']}: {e}")
        return
    
    # Create mapping: sl_order_tag → order details
    order_book_map = {}
    for order in order_book:
        tag = order.get("OrderUniqueIdentifier")
        if tag:
            order_book_map[tag] = order
    
    # Check each SL order for this strategy
    sl_orders = strategy.get("sl_orders", []) or []
    sl_tag_map = strategy.get("sl_tag_map", {}) or {}
    
    for sl_order in sl_orders:
        tag = sl_order.get("tag")
        
        if not tag or tag not in order_book_map:
            continue
        
        # Get order details from XTS order book
        order_detail = order_book_map[tag]
        order_status = order_detail.get("OrderStatus", "").upper()
        
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
        if order_status == "FILLED":
            # ✅ SL order was executed - position is CLOSED
            try:
                exit_price = float(order_detail.get("OrderAverageTradedPrice", 0.0))
                if exit_price > 0:
                    matching_position["exit_price"] = exit_price
                    matching_position["exit_time"] = get_ist_timestamp()
                    matching_position["closed_via"] = "SL_FILLED"
                    
                    logger.info(
                        f"✅ [{strategy['name']}] Position CLOSED via SL: "
                        f"Instrument {instrument_id}, Exit Price: {exit_price}"
                    )
                    update_order_status(tag, "Filled", exit_price)
                    
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
            except (TypeError, ValueError) as e:
                logger.warning(f"Failed to parse exit price from order {tag}: {e}")
        
        elif order_status in ("REJECTED", "CANCELLED"):
            # ⚠️ SL order failed - position is EXPOSED
            matching_position["sl_status"] = order_status
            logger.warning(
                f"⚠️  [{strategy['name']}] SL order {order_status}: "
                f"Instrument {instrument_id} (position still exposed)"
            )
        
        elif order_status in ("PENDING", "OPEN", "PARTIALLY_FILLED"):
            # ⏳ SL order still active - position still OPEN
            matching_position["sl_status"] = "WAITING"


def _check_leg_target_and_close(
    client: XTSClient, strategy: dict, ltp_map: Dict[int, float]
) -> None:
    """
    If any leg's profit from collected premium reaches LEG_TARGET_PCT (e.g. 65%),
    close that leg by modifying its SL order to market (single action: leg closed + SL order executed).
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

        try:
            client.modify_order(
                app_order_id=app_order_id,
                product_type=order_detail.get("ProductType"),
                order_type=client.interactive.ORDER_TYPE_MARKET,
                quantity=int(order_detail.get("OrderQuantity", 0)),
                disclosed_quantity=int(order_detail.get("OrderDisclosedQuantity", 0)),
                stop_price=0,
                limit_price=0,
                time_in_force=order_detail.get("TimeInForce"),
                tag=tag,
            )
            target_triggered.add(instrument_id)
            update_strategy(strategy["name"], target_triggered_instruments=list(target_triggered))
            logger.info(
                "✅ [%s] Leg target %.1f%% hit (instrument %s, profit %.1f%%); modified SL to market",
                strategy["name"], float(LEG_TARGET_PCT), instrument_id, profit_pct,
            )
        except Exception as e:
            logger.error(
                "Failed to modify SL to market for leg target [%s] instrument %s: %s",
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
    strategy: dict,
    broker_positions: List[dict],
    ltp_map: dict,
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

    # If broker has no net position for an instrument but we still show it open locally, close it.
    db_id = strategy.get("db_id")
    now_ts = get_ist_timestamp()
    for pos in local_positions:
        instrument_id = int(pos.get("instrument_id") or 0)
        if instrument_id == 0:
            continue
        if pos.get("exit_price") is not None:
            continue

        broker_qty = broker_map.get(instrument_id, 0)  # missing => 0 (position fully squared off)
        if broker_qty == 0:
            exit_price = ltp_map.get(instrument_id)
            if exit_price is None:
                # Fallback: no LTP available; assume flat exit so UI doesn't show it as open forever
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


def _monitor_mtm(client: XTSClient, index_config, portfolio_sl: float) -> None:
    positions = client.get_positions()
    instruments = [
        {"exchangeSegment": index_config.option_ltp_segment, "exchangeInstrumentID": pos["ExchangeInstrumentId"]}
        for pos in positions
        if int(pos["ExchangeInstrumentId"]) != 0
    ]
    ltp_map = client.get_ltp_map(instruments)
    realized, unrealized, overall = calculate_mtm(positions, ltp_map)
    update_portfolio(overall, realized, unrealized, portfolio_sl)

    for strategy in STRATEGY_STATE.values():
        # **FIRST**: Sync SL order status from XTS order book (single source of truth)
        _sync_sl_order_status_and_capture_exits(client, strategy)

        # **LEG TARGET**: If any leg has reached target % profit, close it by modifying SL to market
        _check_leg_target_and_close(client, strategy, ltp_map)
        
        # Sync strategy positions with broker positions to keep state real-time
        _sync_strategy_positions_from_broker(strategy, positions, ltp_map)
        
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
            logger.info(f"✅ [{strategy['name']}] All positions closed via SL orders - closing strategy")
            _close_strategy(
                client, index_config, strategy, positions,
                "All positions closed via SL orders",
            )

        # Now s_total is based on local positions (exit_price/LTP), not broker aggregates.
        if strategy["status"] == "OPEN" and s_total <= -float(strategy["strategy_sl"]):
            _close_strategy(client, index_config, strategy, positions, "Strategy SL hit")

    if overall <= portfolio_sl:
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


def _update_available_margin(client: XTSClient) -> None:
    try:
        available_margin = client.get_available_margin()
    except Exception:
        logger.exception("Failed to fetch available margin")
        return
    update_portfolio_margin(available_margin)


STRATEGY_STATE: Dict[str, dict] = {
    cfg.name: {
        "name": cfg.name, "time": cfg.time, "lots": cfg.lots,
        "leg_sl_pct": cfg.leg_sl_pct, "leg_target_pct": LEG_TARGET_PCT, "strategy_sl": cfg.strategy_sl,
        "status": "PENDING", "mtm": 0.0, "realized": 0.0, "unrealized": 0.0,
        "strike": None, "instrument_ids": [], "sl_orders": [], "positions": [],
        "order_tags": [], "entry_time": None, "message": None, "last_update": None,
        "sl_tag_map": {}, "db_id": None,
    }
    for cfg in STRATEGIES
}


def _apply_non_expiry_overrides(expiry: str) -> None:
    """If today is NOT expiry day, override lots and leg_sl_pct in STRATEGY_STATE for the UI."""
    if _is_expiry_day(expiry):
        return
    for strategy in STRATEGY_STATE.values():
        update_strategy(
            strategy["name"],
            lots=int(STRATEGY_LOTS_NON_EXPIRY),
            leg_sl_pct=float(LEG_SL_PCT_NON_EXPIRY),
        )
    logger.info(
        "Non-expiry day: overridden lots=%d, leg_sl_pct=%.1f%% for all strategies",
        STRATEGY_LOTS_NON_EXPIRY,
        LEG_SL_PCT_NON_EXPIRY,
    )


def _schedule_jobs(client: XTSClient, index_config, expiry: str) -> None:
    _apply_non_expiry_overrides(expiry)

    if not _is_expiry_day(expiry) and not TRADE_NON_EXPIRY_DAY:
        logger.info("Non-expiry day and TRADE_NON_EXPIRY_DAY is disabled — skipping all strategy scheduling")
        for strategy in STRATEGY_STATE.values():
            update_strategy(strategy["name"], status="DISABLED", message="No trading on non-expiry day")
        schedule.every(60).seconds.do(_update_available_margin, client=client)
        schedule.every().day.at("00:00").do(cleanup_old_data)
        return

    test_mode = os.getenv("TEST_FIRST_STRATEGY_IN_1MIN", "false").lower() == "true"
    
    for idx, strategy in enumerate(STRATEGY_STATE.values()):
        if idx == 0 and test_mode:
            logger.info("TEST MODE: First strategy in 1 minute")
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
    schedule.every(60).seconds.do(_update_available_margin, client=client)
    schedule.every().day.at("00:00").do(cleanup_old_data)


def _retry_pick_expiry(client: XTSClient, auth: dict) -> None:
    """Periodically retry picking expiry if initial attempt failed."""
    global STRATEGY_STATE
    retry_interval = 10  # Start with 10 seconds
    max_interval = 60    # Max out at 60 seconds
    attempt = 0
    
    while True:
        time.sleep(retry_interval)
        attempt += 1
        
        try:
            logger.info(f"🔄 Retry {attempt}: Checking for expiry data...")
            index_config, expiry = _pick_index_and_expiry(client)
            
            logger.info(f"✓ Expiry found! Index: {index_config.name} | Expiry: {expiry}")
            set_index(index_config.name, expiry)
            
            # Schedule jobs now that we have expiry
            _schedule_jobs(client, index_config, expiry)
            
            # Restore today's strategies now that we have expiry
            restored = restore_todays_strategies()
            for restored_strategy in restored:
                strategy_name = restored_strategy["strategy_name"]
                if strategy_name in STRATEGY_STATE:
                    logger.info(f"Restoring {strategy_name} from database...")
                    STRATEGY_STATE[strategy_name].update({
                        "db_id": restored_strategy["db_id"],
                        "status": restored_strategy.get("status", "OPEN"),
                        "strike": restored_strategy["strike"],
                        "entry_time": restored_strategy["entry_time"],
                        "positions": restored_strategy["positions"],
                    })
            
            logger.info("✓ Bot is now operational")
            return  # Success, exit retry loop
            
        except RuntimeError as e:
            # Update error message on UI
            error_msg = f"Still waiting for expiry data... (Attempt {attempt})"
            set_index_error(error_msg)
            logger.debug(f"Retry {attempt} failed: {e}")
            
            # Increase retry interval, but cap at max
            retry_interval = min(retry_interval + 5, max_interval)
            current_time = get_ist_now().strftime("%H:%M:%S")
            logger.info(f"⏳ Retrying in {retry_interval}s... Current time: {current_time} (Market hours: 09:15-15:30)")


def main() -> None:
    if DEMO_MODE:
        logger.info("DEMO MODE - Simulated data")
        client = None
        index_config = INDEX_CONFIGS["NIFTY"]
        expiry = "08FEB2026"
        auth = {"username": "admin", "password": "admin123"}
    else:
        if not ACC_NAME:
            required_env = (
                "XTS_API_KEY_5P",
                "XTS_API_SECRET_5P",
                "XTS_5P_CLIENTID_5P",
                "XTS_MARKET_API_KEY_5P",
                "XTS_MARKET_API_SECRET_5P",
                "LOGIN_USERNAME_5P",
                "LOGIN_PASSWORD_5P",
            )
            missing = [key for key in required_env if not os.getenv(key)]
            if missing:
                raise RuntimeError(
                    "ACC_NAME is required for SSM lookups; set ACC_NAME or provide all credential env vars. "
                    f"Missing: {', '.join(missing)}"
                )
        creds = load_credentials()
        auth = get_basic_auth_creds(creds)
        client = XTSClient(
            api_key=creds["api_key"],
            api_secret=creds["api_secret"],
            market_api_key=creds["market_api_key"],
            market_api_secret=creds["market_api_secret"],
            source=SOURCE,
            client_id=creds["client_id"],
        )
        client.login()
        try:
            index_config, expiry = _pick_index_and_expiry(client)
        except RuntimeError as e:
            logger.error(f"Failed to pick index and expiry: {e}")
            index_config = None
            expiry = None
            client_for_retry = client

    logger.info("Index: %s | Expiry: %s", index_config.name if index_config else "NOT SET", expiry if expiry else "NOT SET")
    init_db()
    # Clean up all previous day data on startup
    cleanup_previous_day_data()
    
    # Restore today's strategies from database (before init_state)
    if not DEMO_MODE and index_config is not None:
        restored = restore_todays_strategies()
        for restored_strategy in restored:
            strategy_name = restored_strategy["strategy_name"]
            if strategy_name in STRATEGY_STATE:
                logger.info(f"Restoring {strategy_name} from database...")
                STRATEGY_STATE[strategy_name].update({
                    "db_id": restored_strategy["db_id"],
                    "status": restored_strategy.get("status", "OPEN"),
                    "strike": restored_strategy["strike"],
                    "entry_time": restored_strategy["entry_time"],
                    "positions": restored_strategy["positions"],
                })
    
    init_state(STRATEGY_STATE)
    set_mtm_snapshots_enabled(DB_ENABLE_MTM_SNAPSHOTS)
    if index_config is not None:
        set_index(index_config.name, expiry)
    else:
        set_index_error("No expiries found for NIFTY or SENSEX. Please check market hours (09:15-15:30).")

    app = create_app(auth["username"], auth["password"])
    
    # Only schedule jobs if we have valid index and expiry
    jobs_scheduled = False
    if not DEMO_MODE and index_config is not None:
        _schedule_jobs(client, index_config, expiry)
        jobs_scheduled = True

    from threading import Thread
    ui_thread = Thread(target=lambda: app.run(host="0.0.0.0", port=80, debug=False, use_reloader=False))
    ui_thread.daemon = True
    ui_thread.start()

    logger.info("UI at http://localhost:8001 | %s / %s", auth["username"], auth["password"])
    
    # If no expiry was found, retry periodically
    if index_config is None and not DEMO_MODE:
        logger.info("⏳ Waiting for expiry data... Current time: %s (Market hours: 09:15-15:30)", get_ist_now().strftime("%H:%M:%S"))
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
        while True:
            time.sleep(1)
    else:
        while True:
            schedule.run_pending()
            time.sleep(1)


if __name__ == "__main__":
    main()
