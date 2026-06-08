"""Margin check, hedging, and lot sizing."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from config import (
    HEDGE_ON_EVERY_STRATEGY,
    HEDGE_PREMIUM_MAX_EXPIRY,
    HEDGE_PREMIUM_MAX_NON_EXPIRY,
    HEDGE_PREMIUM_MIN_EXPIRY,
    HEDGE_PREMIUM_MIN_NON_EXPIRY,
    HEDGE_TARGET_PREMIUM_EXPIRY,
    HEDGE_TARGET_PREMIUM_NON_EXPIRY,
    MARGIN_BUFFER_EXPIRY,
    MARGIN_BUFFER_NON_EXPIRY,
    MARGIN_REQUIRED_PER_LOT_NON_EXPIRY,
    MARGIN_TIGHT_BUFFER_MIN,
    margin_required_per_lot_expiry,
)
from state import update_portfolio_margin
from trading.compat import resolve
from trading.journal import Phase, record as journal
from trading.strategy.strikes import find_hedge_by_target_premium
from trading.state_bridge import update_strategy
from trading.utils import (
    bot_tracked_hedge_buy_qty_by_side,
    bot_tracked_open_short_qty_by_side,
    compute_effective_lots_from_margin,
    is_expiry_day,
)

logger = logging.getLogger("xts-bot-lite")


def ensure_margin_or_skip_strategy(
    client: Any,
    index_config,
    expiry: str,
    strategy: dict,
    atm_strike: int,
) -> bool:
    name = strategy["name"]
    is_exp = is_expiry_day(expiry)
    available = client.get_available_margin()
    update_portfolio_margin(available)
    planned_lots = int(strategy.get("lots") or 0)
    per_lot = (
        margin_required_per_lot_expiry(getattr(index_config, "name", None))
        if is_exp
        else float(MARGIN_REQUIRED_PER_LOT_NON_EXPIRY)
    )
    base_buf = float(MARGIN_BUFFER_EXPIRY) if is_exp else float(MARGIN_BUFFER_NON_EXPIRY)
    required_for_planned = per_lot * planned_lots + base_buf
    lot_size = int(index_config.lot_size)
    entry_qty = planned_lots * lot_size

    journal(
        Phase.MARGIN_CHECK,
        name,
        f"Margin check: available={available}, required={required_for_planned:.0f} for {planned_lots} lots",
        available=available,
        required=required_for_planned,
        planned_lots=planned_lots,
        per_lot=per_lot,
        buffer=base_buf,
    )

    need_hedge = bool(resolve("HEDGE_ON_EVERY_STRATEGY", HEDGE_ON_EVERY_STRATEGY)) or (
        available is not None and float(available) < required_for_planned
    )
    if need_hedge:
        ce_short_qty, pe_short_qty = bot_tracked_open_short_qty_by_side()
        pe_hedged_qty, ce_hedged_qty = bot_tracked_hedge_buy_qty_by_side()
        planned_entry_qty = entry_qty if resolve("HEDGE_ON_EVERY_STRATEGY", HEDGE_ON_EVERY_STRATEGY) else 0
        pe_hedge_qty = max(0, int(ce_short_qty) + int(planned_entry_qty) - int(pe_hedged_qty))
        ce_hedge_qty = max(0, int(pe_short_qty) + int(planned_entry_qty) - int(ce_hedged_qty))

        target_premium = float(HEDGE_TARGET_PREMIUM_EXPIRY) if is_exp else float(HEDGE_TARGET_PREMIUM_NON_EXPIRY)
        min_premium = float(HEDGE_PREMIUM_MIN_EXPIRY) if is_exp else float(HEDGE_PREMIUM_MIN_NON_EXPIRY)
        max_premium = float(HEDGE_PREMIUM_MAX_EXPIRY) if is_exp else float(HEDGE_PREMIUM_MAX_NON_EXPIRY)

        if pe_hedge_qty > 0 or ce_hedge_qty > 0:
            journal(
                Phase.HEDGE_PLACED,
                name,
                f"Placing hedges PE qty={pe_hedge_qty} CE qty={ce_hedge_qty}",
                pe_hedge_qty=pe_hedge_qty,
                ce_hedge_qty=ce_hedge_qty,
                premium_band=[min_premium, max_premium],
            )
            all_hedge_orders: List[dict] = list(strategy.get("hedge_orders") or [])
            hedge_strikes: Dict[str, Optional[int]] = dict(strategy.get("hedge_strikes") or {"PE": None, "CE": None})
            hedge_side_qty: Dict[str, int] = {"PE": pe_hedged_qty, "CE": ce_hedged_qty}

            pe_hedge = (
                resolve("_find_hedge_by_target_premium", find_hedge_by_target_premium)(
                    client, index_config, expiry, "PE", atm_strike, target_premium, min_premium, max_premium
                )
                if pe_hedge_qty > 0
                else None
            )
            ce_hedge = (
                resolve("_find_hedge_by_target_premium", find_hedge_by_target_premium)(
                    client, index_config, expiry, "CE", atm_strike, target_premium, min_premium, max_premium
                )
                if ce_hedge_qty > 0
                else None
            )
            if (pe_hedge_qty > 0 and not pe_hedge) or (ce_hedge_qty > 0 and not ce_hedge):
                journal(Phase.CRITERIA_FAILED, name, "Unable to find hedge options", severity="ERROR")
                update_strategy(name, status="ERROR", message="Unable to find hedge options")
                return False

            instruments = []
            if pe_hedge and pe_hedge_qty > 0:
                instruments.append({"exchangeSegment": index_config.option_ltp_segment, "exchangeInstrumentID": int(pe_hedge["instrument_id"])})
                hedge_strikes["PE"] = int(pe_hedge.get("strike"))
            if ce_hedge and ce_hedge_qty > 0:
                instruments.append({"exchangeSegment": index_config.option_ltp_segment, "exchangeInstrumentID": int(ce_hedge["instrument_id"])})
                hedge_strikes["CE"] = int(ce_hedge.get("strike"))
            hedge_ltps = client.get_ltp_map(instruments) if instruments else {}

            placed_now: List[dict] = []
            for hedge, side, qty in ((pe_hedge, "PE", pe_hedge_qty), (ce_hedge, "CE", ce_hedge_qty)):
                if not hedge or qty <= 0:
                    continue
                tag = f"{name}_HEDGE_{side}_BUY_{int(time.time())}"
                iid = int(hedge["instrument_id"])
                oid = client.place_market_order(
                    index_config=index_config,
                    instrument_id=iid,
                    order_side=client.interactive.TRANSACTION_TYPE_BUY,
                    quantity=qty,
                    tag=tag,
                    product_type=client.interactive.PRODUCT_MIS,
                    ltp=hedge_ltps.get(iid),
                )
                if oid:
                    order_rec = {"app_order_id": oid, "tag": tag, "instrument_id": iid, "quantity": qty, "side": side}
                    placed_now.append(order_rec)
                    all_hedge_orders.append(order_rec)
                    hedge_side_qty[side] = int(hedge_side_qty.get(side) or 0) + int(qty)
                else:
                    close_fn = resolve("_close_positions_for_instruments", None)
                    for placed in placed_now:
                        try:
                            client.cancel_order(placed["app_order_id"], placed["tag"])
                        except Exception:
                            logger.exception("Failed to cancel hedge order %s", placed)
                    journal(Phase.CRITERIA_FAILED, name, "Hedge order placement failed", severity="ERROR")
                    update_strategy(name, status="ERROR", message="Hedge order placement failed")
                    return False

            if placed_now:
                update_strategy(
                    name,
                    hedge_orders=all_hedge_orders,
                    hedge_target_premium=target_premium,
                    hedge_qty=sum(int(o.get("quantity") or 0) for o in all_hedge_orders),
                    hedge_side_qty=hedge_side_qty,
                    hedge_strikes=hedge_strikes,
                    message="Far-OTM hedge orders placed; rechecking margin",
                )
                time.sleep(3)
                available = client.get_available_margin()
                update_portfolio_margin(available)

    effective_lots, sizing_reason = compute_effective_lots_from_margin(
        planned_lots=planned_lots,
        available_margin=available,
        per_lot_margin=per_lot,
        buffer=base_buf,
        tight_buffer=float(MARGIN_TIGHT_BUFFER_MIN),
    )
    if effective_lots <= 0:
        msg = (
            f"MARGIN_NOT_AVAILABLE: no safe lots after hedge/margin check "
            f"(planned {planned_lots}, available {available}, per_lot {per_lot:.0f})"
        )
        journal(Phase.CRITERIA_FAILED, name, msg, severity="ERROR")
        update_strategy(name, status="ERROR", message=msg)
        return False
    if effective_lots != planned_lots:
        journal(
            Phase.LOTS_SIZED,
            name,
            sizing_reason or f"Lots resized {planned_lots} -> {effective_lots}",
            planned_lots=planned_lots,
            effective_lots=effective_lots,
            available=available,
        )
        update_strategy(name, lots=effective_lots, planned_entry_lots=planned_lots, message=sizing_reason)
        strategy["lots"] = effective_lots
    else:
        journal(Phase.LOTS_SIZED, name, f"Executing {effective_lots} lot(s)", effective_lots=effective_lots)
    return True
