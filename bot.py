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
    PORTFOLIO_SL_LIMIT,
    SOURCE,
    STRATEGIES,
    DEMO_MODE,
    get_basic_auth_creds,
    load_credentials,
)
from mtm import calculate_mtm, calculate_strategy_mtm
from state import init_state, set_index, set_spot, update_portfolio, update_strategy
from ui import create_app
from xts_client import XTSClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")],
)
logger = logging.getLogger("xts-bot-lite")
APP_START_TIME = datetime.datetime.now()


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


def _round_to_tick(price: float, tick_size: float = 0.05) -> float:
    """Round price to nearest tick size (required by XTS API)."""
    return math.ceil(price / tick_size) * tick_size


def _place_leg_sl_orders(
    client: XTSClient,
    index_config,
    filled_orders: List[dict],
    leg_sl_pct: float,
    strategy_name: str,
) -> List[dict]:
    sl_orders = []
    for order in filled_orders:
        # Calculate raw SL price
        raw_price = float(order["OrderAverageTradedPrice"]) * (1 + leg_sl_pct / 100.0)
        
        # Round to nearest tick size to comply with XTS API
        price = _round_to_tick(raw_price, index_config.tick_size)
        trigger = _round_to_tick(max(price - 0.5, 0.05), index_config.tick_size)
        
        tag = f"{strategy_name}_SL_{order['TradingSymbol']}_{int(time.time())}"
        order_id = client.place_sl_order(
            index_config=index_config,
            instrument_id=int(order["ExchangeInstrumentID"]),
            order_side=client.interactive.TRANSACTION_TYPE_BUY,
            quantity=int(order["OrderQuantity"]),
            limit_price=price,
            stop_price=trigger,
            tag=tag,
            product_type=order["ProductType"],
        )
        if order_id:
            sl_orders.append({"app_order_id": order_id, "tag": tag})
    return sl_orders


def _get_filled_orders(order_book: List[dict], tags: List[str]) -> List[dict]:
    return [
        order
        for order in order_book
        if order.get("OrderUniqueIdentifier") in tags and order.get("OrderStatus") == "Filled"
    ]


def _execute_strategy(client: XTSClient, index_config, expiry: str, strategy, force: bool = False) -> None:
    if strategy["status"] not in ("PENDING", "ERROR"):
        return
    if not force and datetime.datetime.now().strftime("%H:%M:%S") < strategy["time"]:
        return

    name = strategy["name"]
    strike = _get_atm_strike(client, index_config)
    if strike is None:
        update_strategy(name, status="ERROR", message="Spot LTP unavailable")
        return

    ce_id = client.get_option_instrument_id(index_config, expiry, "CE", strike)
    pe_id = client.get_option_instrument_id(index_config, expiry, "PE", strike)
    if not ce_id or not pe_id:
        update_strategy(name, status="ERROR", message="Option instruments not found")
        return

    qty = strategy["lots"] * index_config.lot_size
    ce_tag = f"{name}_CE_SELL_{int(time.time())}"
    pe_tag = f"{name}_PE_SELL_{int(time.time())}"

    for instrument_id, tag in [(ce_id, ce_tag), (pe_id, pe_tag)]:
        client.place_market_order(
            index_config=index_config,
            instrument_id=instrument_id,
            order_side=client.interactive.TRANSACTION_TYPE_SELL,
            quantity=qty,
            tag=tag,
            product_type=client.interactive.PRODUCT_MIS,
        )

    update_strategy(
        name,
        status="OPEN",
        strike=strike,
        instrument_ids=[ce_id, pe_id],
        entry_time=datetime.datetime.now().isoformat(timespec="seconds"),
        order_tags=[ce_tag, pe_tag],
    )

    time.sleep(5)
    filled = _get_filled_orders(client.get_order_book(), [ce_tag, pe_tag])
    sl_orders = _place_leg_sl_orders(client, index_config, filled, strategy["leg_sl_pct"], name)
    positions = []
    for order in filled:
        try:
            instrument_id = int(order.get("ExchangeInstrumentID", 0))
            quantity = -abs(int(order.get("OrderQuantity", 0)))
            entry_price = float(order.get("OrderAverageTradedPrice", 0.0))
        except (TypeError, ValueError):
            continue
        positions.append(
            {
                "instrument_id": instrument_id,
                "quantity": quantity,
                "entry_price": entry_price,
                "symbol": order.get("TradingSymbol"),
            }
        )
    update_strategy(name, sl_orders=sl_orders, positions=positions)


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


def _close_strategy(client: XTSClient, index_config, strategy: dict, positions: List[dict], reason: str) -> None:
    if strategy["status"] in ("CLOSED", "CLOSING"):
        return
    update_strategy(strategy["name"], status="CLOSING", message=reason)
    _close_positions_for_instruments(client, index_config, positions, strategy.get("instrument_ids", []))
    _cancel_strategy_sl_orders(client, strategy)
    update_strategy(strategy["name"], status="CLOSED", positions=[])


def _square_off_all(client: XTSClient, index_config, positions: List[dict], reason: str) -> None:
    logger.warning("Square-off all positions: %s", reason)
    for pos in positions:
        _place_close_order(client, index_config, pos, "SQUAREOFF")


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
        strategy_positions = strategy.get("positions") or []
        if not strategy_positions:
            continue
        s_realized, s_unrealized, s_total = calculate_strategy_mtm(strategy_positions, ltp_map)
        update_strategy(strategy["name"], mtm=s_total, realized=s_realized, unrealized=s_unrealized)
        if strategy["status"] == "OPEN" and s_total <= -float(strategy["strategy_sl"]):
            _close_strategy(client, index_config, strategy, positions, "Strategy SL hit")

    if overall <= portfolio_sl:
        _square_off_all(client, index_config, positions, "Portfolio SL hit")
        for strategy in STRATEGY_STATE.values():
            update_strategy(strategy["name"], status="CLOSED", message="Portfolio SL hit", positions=[])


STRATEGY_STATE: Dict[str, dict] = {
    cfg.name: {
        "name": cfg.name, "time": cfg.time, "lots": cfg.lots,
        "leg_sl_pct": cfg.leg_sl_pct, "strategy_sl": cfg.strategy_sl,
        "status": "PENDING", "mtm": 0.0, "realized": 0.0, "unrealized": 0.0,
        "strike": None, "instrument_ids": [], "sl_orders": [], "positions": [],
        "order_tags": [], "entry_time": None, "message": None, "last_update": None,
    }
    for cfg in STRATEGIES
}


def _schedule_jobs(client: XTSClient, index_config, expiry: str) -> None:
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
        index_config, expiry = _pick_index_and_expiry(client)

    logger.info("Index: %s | Expiry: %s", index_config.name, expiry)
    init_state(STRATEGY_STATE)
    set_index(index_config.name, expiry)

    app = create_app(auth["username"], auth["password"])
    if not DEMO_MODE:
        _schedule_jobs(client, index_config, expiry)

    from threading import Thread
    ui_thread = Thread(target=lambda: app.run(host="0.0.0.0", port=80, debug=False, use_reloader=False))
    ui_thread.daemon = True
    ui_thread.start()

    logger.info("UI at http://localhost:8001 | %s / %s", auth["username"], auth["password"])

    if DEMO_MODE:
        update_portfolio(2500.50, 1200.25, 1300.25, PORTFOLIO_SL_LIMIT)
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
