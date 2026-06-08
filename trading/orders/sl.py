"""Stop-loss order placement and verification."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from db import log_order
from trading.journal import Phase, record as journal
from trading.state_bridge import update_strategy
from trading.utils import round_to_tick

logger = logging.getLogger("xts-bot-lite")


def place_leg_sl_orders(
    client: Any,
    index_config,
    filled_orders: List[dict],
    leg_sl_pct: float,
    strategy_name: str,
) -> Tuple[List[dict], Dict[str, int]]:
    sl_orders: List[dict] = []
    tag_to_instrument: Dict[str, int] = {}
    for order in filled_orders:
        try:
            quantity = int(order.get("OrderQuantityTraded") or order.get("OrderQuantity") or 0)
        except (TypeError, ValueError):
            quantity = 0
        if quantity <= 0:
            continue

        entry_px = float(order["OrderAverageTradedPrice"])
        raw_price = entry_px * (1 + leg_sl_pct / 100.0)
        price = round_to_tick(raw_price, index_config.tick_size)
        trigger = round_to_tick(max(price - 0.5, 0.05), index_config.tick_size)
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
                int(order_id),
                tag,
                instrument_id,
                order.get("TradingSymbol", ""),
                quantity,
                "STOPLIMIT",
                "BUY",
            )
        else:
            journal(
                Phase.SL_REJECTED,
                strategy_name,
                f"SL placement failed for instrument {instrument_id}",
                severity="ERROR",
                instrument_id=instrument_id,
                entry_price=entry_px,
            )
    return sl_orders, tag_to_instrument


def verify_sl_orders_live(
    client: Any,
    strategy_name: str,
    sl_orders: List[dict],
    *,
    max_wait_seconds: int = 12,
    poll_interval: int = 2,
) -> Tuple[bool, str]:
    if not sl_orders:
        return False, "no_sl_orders_created"
    want_ids = set()
    for o in sl_orders:
        try:
            oid = int(o.get("app_order_id"))
        except (TypeError, ValueError):
            oid = None
        if oid is not None:
            want_ids.add(oid)
    if not want_ids:
        return False, "sl_order_ids_missing"

    attempts = max(1, int(max_wait_seconds // max(1, poll_interval)))
    last_missing = want_ids
    for _ in range(attempts):
        try:
            book = client.get_order_book()
        except Exception as e:
            logger.warning("[%s] Could not fetch order book for SL verify: %s", strategy_name, e)
            time.sleep(poll_interval)
            continue
        by_id: Dict[int, dict] = {}
        for od in book or []:
            try:
                oid = od.get("AppOrderID")
                if oid is None:
                    continue
                by_id[int(oid)] = od
            except Exception:
                continue

        missing = set()
        for oid in want_ids:
            od = by_id.get(oid)
            if not od:
                missing.add(oid)
                continue
            st_raw = str(od.get("OrderStatus") or "")
            st = st_raw.replace(" ", "").replace("_", "").upper()
            if st in ("REJECTED", "CANCELLED", "CANCELED"):
                return False, f"sl_bad_status({st_raw})"

        if not missing:
            return True, "sl_verified_in_order_book"

        last_missing = missing
        time.sleep(poll_interval)

    return False, f"sl_missing_in_order_book(count={len(last_missing)})"


def rebuild_sl_links_from_order_book(
    strategy: dict, order_book: Optional[List[dict]]
) -> bool:
    """Rebuild SL links from broker order book tags like '<strategy>_SL_<instrument>'."""
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
