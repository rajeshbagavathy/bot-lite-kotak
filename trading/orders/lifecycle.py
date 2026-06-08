"""
Entry → SL protection pipeline with full journal trail.

Fixes naked-leg risk: if fills/SL fail, flatten exposure instead of leaving OPEN without SL.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from config import LEG_TARGET_PCT
from db import get_ist_timestamp, log_position, update_order_status
from trading.compat import resolve
from trading.journal import Phase, record as journal
from trading.orders.book import get_filled_orders
from trading.orders.close import close_positions_for_instruments, positions_exposure_for_instruments
from trading.orders.sl import place_leg_sl_orders, verify_sl_orders_live
from trading.state_bridge import update_strategy

logger = logging.getLogger("xts-bot-lite")

ENTRY_FILL_MAX_WAIT_SEC = 45
ENTRY_FILL_POLL_SEC = 3
SL_VERIFY_MAX_WAIT_SEC = 12
SL_VERIFY_POLL_SEC = 2


@dataclass
class SlProtectionResult:
    ok: bool
    sl_orders: List[dict] = field(default_factory=list)
    sl_tag_map: Dict[str, int] = field(default_factory=dict)
    filled_orders: List[dict] = field(default_factory=list)
    positions: List[dict] = field(default_factory=list)
    error_message: Optional[str] = None
    flattened: bool = False


def wait_for_entry_fills(
    client: Any,
    strategy_name: str,
    entry_app_order_ids: List[int],
    expected_legs: int,
) -> Tuple[List[dict], str]:
    journal(
        Phase.ENTRY_FILL_WAIT,
        strategy_name,
        f"Waiting up to {ENTRY_FILL_MAX_WAIT_SEC}s for {expected_legs} entry fill(s)",
        entry_app_order_ids=entry_app_order_ids,
    )
    filled: List[dict] = []
    max_attempts = max(1, int(ENTRY_FILL_MAX_WAIT_SEC // ENTRY_FILL_POLL_SEC))
    for attempt in range(max_attempts):
        try:
            order_book = client.get_order_book()
        except Exception as e:
            logger.error("[%s] Order book fetch failed during fill wait: %s", strategy_name, e)
            journal(
                Phase.ENTRY_FILL_WAIT,
                strategy_name,
                f"Order book error: {e}",
                severity="ERROR",
                attempt=attempt + 1,
            )
            break
        filled = resolve("_get_filled_orders", get_filled_orders)(order_book, entry_app_order_ids)
        if len(filled) >= expected_legs:
            for order in filled:
                oid = order.get("AppOrderID")
                price = order.get("OrderAverageTradedPrice")
                if oid is not None:
                    update_order_status(
                        app_order_id=int(oid),
                        status="Filled",
                        traded_price=float(price) if price is not None else None,
                    )
            journal(
                Phase.ENTRY_FILLED,
                strategy_name,
                f"All {len(filled)} entry leg(s) confirmed in order book",
                filled_ids=[o.get("AppOrderID") for o in filled],
                entry_prices=[o.get("OrderAverageTradedPrice") for o in filled],
                symbols=[o.get("TradingSymbol") for o in filled],
            )
            return filled, "filled"
        if attempt < max_attempts - 1:
            time.sleep(ENTRY_FILL_POLL_SEC)
    journal(
        Phase.ENTRY_FILL_TIMEOUT,
        strategy_name,
        f"Fill not confirmed after {ENTRY_FILL_MAX_WAIT_SEC}s",
        severity="WARNING",
        partial_fills=len(filled),
        entry_app_order_ids=entry_app_order_ids,
    )
    return filled, "timeout"


def flatten_exposure(
    client: Any,
    index_config,
    strategy_name: str,
    instrument_ids: List[int],
    reason: str,
) -> bool:
    journal(
        Phase.SAFETY_FLATTEN,
        strategy_name,
        reason,
        severity="ERROR",
        instrument_ids=instrument_ids,
    )
    try:
        positions = client.get_positions()
        close_positions_for_instruments(
            client,
            index_config,
            positions,
            instrument_ids,
            strategy_name=strategy_name,
            flow="safety_flatten",
        )
        return True
    except Exception:
        logger.exception("[%s] Flatten failed", strategy_name)
        journal(
            Phase.SAFETY_FLATTEN,
            strategy_name,
            "Flatten order placement failed — check broker manually",
            severity="CRITICAL",
        )
        return False


def complete_entry_with_sl_protection(
    client: Any,
    index_config,
    strategy: dict,
    placed_entry: List[dict],
    leg_sl_pct: float,
    leg_target_pct: float | None = None,
) -> SlProtectionResult:
    name = strategy["name"]
    entry_ids = [int(p["app_order_id"]) for p in placed_entry if p.get("app_order_id") is not None]
    instruments = [int(p["instrument_id"]) for p in placed_entry if p.get("instrument_id") is not None]
    target_pct = float(leg_target_pct if leg_target_pct is not None else LEG_TARGET_PCT)

    time.sleep(5)
    filled, wait_status = wait_for_entry_fills(client, name, entry_ids, len(placed_entry))

    if wait_status != "filled" or not filled:
        exposure: Dict[int, int] = {}
        try:
            broker_positions = client.get_positions()
            exposure = positions_exposure_for_instruments(broker_positions, instruments)
        except Exception:
            logger.exception("[%s] Could not read positions after fill timeout", name)

        if exposure:
            flatten_exposure(
                client,
                index_config,
                name,
                list(exposure.keys()),
                "Broker shows exposure but entry fills not confirmed — flattening",
            )
            msg = "SAFETY STOP: entry exposure without confirmed fills/SL. Flatten attempted."
            update_strategy(name, status="ERROR", message=msg)
            journal(Phase.STRATEGY_ABORT, name, msg, severity="ERROR", exposure=exposure)
            return SlProtectionResult(ok=False, error_message=msg, flattened=True)

        msg = "Entry fill not confirmed within timeout; strategy aborted (no exposure detected)."
        update_strategy(name, status="ERROR", message=msg)
        journal(Phase.STRATEGY_ABORT, name, msg, severity="WARNING")
        return SlProtectionResult(ok=False, error_message=msg)

    sl_orders, tag_map = resolve("_place_leg_sl_orders", place_leg_sl_orders)(
        client, index_config, filled, leg_sl_pct, name
    )
    journal(
        Phase.SL_SENT,
        name,
        f"Placed {len(sl_orders)} SL order(s)",
        sl_order_ids=[o.get("app_order_id") for o in sl_orders],
        tags=list(tag_map.keys()),
    )

    ok, why = resolve("_verify_sl_orders_live", verify_sl_orders_live)(
        client,
        name,
        sl_orders,
        max_wait_seconds=SL_VERIFY_MAX_WAIT_SEC,
        poll_interval=SL_VERIFY_POLL_SEC,
    )
    journal(
        Phase.SL_VERIFY,
        name,
        f"SL verification {'passed' if ok else 'FAILED'}: {why}",
        severity="INFO" if ok else "ERROR",
        sl_count=len(sl_orders),
    )

    if not ok:
        inst_ids = [
            int(o.get("ExchangeInstrumentID") or 0)
            for o in filled
            if o.get("ExchangeInstrumentID")
        ]
        flatten_exposure(client, index_config, name, inst_ids, f"SL not confirmed live ({why}) — flattening")
        msg = f"SAFETY STOP: SL not confirmed live ({why}). Flatten attempted."
        update_strategy(name, status="ERROR", message=msg)
        journal(
            Phase.SL_MISSING if "missing" in why else Phase.SL_REJECTED,
            name,
            msg,
            severity="ERROR",
        )
        return SlProtectionResult(ok=False, error_message=msg, flattened=True)

    positions = []
    for order in filled:
        try:
            instrument_id = int(order.get("ExchangeInstrumentID", 0))
            quantity = -abs(int(order.get("OrderQuantity", 0)))
            entry_price = float(order.get("OrderAverageTradedPrice", 0.0))
        except (TypeError, ValueError):
            continue
        target_price = round(entry_price * (1 - target_pct / 100.0), 2)
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
        if strategy.get("db_id") and strategy["db_id"] > 0:
            log_position(
                strategy["db_id"],
                order.get("TradingSymbol"),
                instrument_id,
                quantity,
                entry_price,
                get_ist_timestamp(),
            )

    journal(
        Phase.PROTECTED,
        name,
        f"Strategy protected: {len(positions)} leg(s) with verified SL",
        sl_orders=[o.get("app_order_id") for o in sl_orders],
        positions=[
            {"symbol": p.get("symbol"), "entry": p.get("entry_price"), "qty": p.get("quantity")}
            for p in positions
        ],
    )
    return SlProtectionResult(
        ok=True,
        sl_orders=sl_orders,
        sl_tag_map=tag_map,
        filled_orders=filled,
        positions=positions,
    )


def enforce_open_strategy_sl_invariant(
    client: Any,
    index_config,
    strategy: dict,
    broker_positions: List[dict],
    order_book: Optional[List[dict]],
) -> bool:
    """
    Monitor-time invariant: OPEN strategy with broker exposure must have live SL orders.
    Returns True if a safety flatten was triggered.
    """
    if strategy.get("status") != "OPEN":
        return False
    instruments = strategy.get("instrument_ids") or [
        p.get("instrument_id") for p in (strategy.get("positions") or [])
    ]
    instruments = [int(i) for i in instruments if i is not None]
    exposure = positions_exposure_for_instruments(broker_positions, instruments)
    if not exposure:
        return False

    name = strategy["name"]
    sls = strategy.get("sl_orders") or []
    if not sls:
        journal(
            Phase.MONITOR_INVARIANT,
            name,
            "Exposure without SL tracking — flattening",
            severity="ERROR",
            exposure=exposure,
        )
        flatten_exposure(client, index_config, name, list(exposure.keys()), "Missing SL orders in state")
        update_strategy(
            name,
            status="ERROR",
            message="SAFETY STOP: open exposure detected without SL orders. Flatten attempted; check broker.",
        )
        return True

    if order_book is None:
        return False

    book_by_id: Dict[int, dict] = {}
    for od in order_book:
        try:
            oid = od.get("AppOrderID")
            if oid is not None:
                book_by_id[int(oid)] = od
        except Exception:
            continue

    bad = False
    bad_reason = ""
    for so in sls:
        try:
            oid = int(so.get("app_order_id"))
        except (TypeError, ValueError):
            bad = True
            bad_reason = "sl_order_id_invalid"
            break
        od = book_by_id.get(oid)
        if not od:
            bad = True
            bad_reason = "sl_missing_in_order_book"
            break
        st = str(od.get("OrderStatus") or "").replace(" ", "").replace("_", "").upper()
        if st in ("REJECTED", "CANCELLED", "CANCELED"):
            bad = True
            bad_reason = f"sl_status_{st}"
            break

    if not bad:
        return False

    journal(
        Phase.MONITOR_INVARIANT,
        name,
        f"Bad SL state ({bad_reason}) with exposure — flattening",
        severity="ERROR",
        exposure=exposure,
    )
    flatten_exposure(
        client,
        index_config,
        name,
        list(exposure.keys()),
        f"SL invariant violated: {bad_reason}",
    )
    update_strategy(
        name,
        status="ERROR",
        message="SAFETY STOP: SL orders missing/rejected/cancelled while exposed. Flatten attempted; check broker.",
    )
    return True
