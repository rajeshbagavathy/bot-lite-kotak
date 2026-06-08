"""Shared trading utilities."""
from __future__ import annotations

import datetime
import math
from typing import Any, Optional, Tuple

from config import INDEX_CONFIGS, MARGIN_REQUIRED_PER_LOT_NON_EXPIRY, MARGIN_TIGHT_BUFFER_MIN, MIN_MARGIN_TO_TRADE, get_today_strategies, margin_required_per_lot_expiry
from db import get_ist_now
from trading.context import STRATEGY_STATE
from trading.state_bridge import set_spot

import logging

logger = logging.getLogger("xts-bot-lite")


def round_to_tick(price: float, tick_size: float = 0.05) -> float:
    return math.ceil(price / tick_size) * tick_size


def pick_index_and_expiry(client: Any) -> Tuple[dict, str]:
    expiry_map = {}
    for config in INDEX_CONFIGS.values():
        if not get_today_strategies(config.name):
            continue
        expiries = client.get_expiry_dates(config)
        if expiries:
            nearest = min(expiries)
            expiry_map[config.name] = nearest
    if not expiry_map:
        scheduled = [c.name for c in INDEX_CONFIGS.values() if get_today_strategies(c.name)]
        if not scheduled:
            raise RuntimeError("No strategies scheduled for today")
        raise RuntimeError("No expiries found for NIFTY or SENSEX")
    earliest = min(expiry_map.values())
    candidates = [name for name, date in expiry_map.items() if date == earliest]
    chosen_name = "SENSEX" if "SENSEX" in candidates else candidates[0]
    expiry = client.format_expiry_for_options(earliest)
    return INDEX_CONFIGS[chosen_name], expiry


def get_atm_strike(client: Any, index_config) -> Optional[int]:
    spot = client.get_spot_ltp(index_config)
    if spot is None:
        return None
    spot_val = round(float(spot), 2)
    set_spot(spot_val)
    return int(round(spot_val / index_config.strike_diff) * index_config.strike_diff)


def is_expiry_day(expiry: str) -> bool:
    try:
        expiry_date = datetime.datetime.strptime(expiry, "%d%b%Y").date()
    except ValueError:
        return False
    return get_ist_now().date() == expiry_date


def compute_effective_lots_from_margin(
    planned_lots: int,
    available_margin: Optional[float],
    per_lot_margin: float,
    buffer: float,
    tight_buffer: float,
) -> Tuple[int, Optional[str]]:
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
        one_lot_required = float(per_lot_margin) + float(buffer)
        if float(available_margin) >= float(MIN_MARGIN_TO_TRADE) and float(available_margin) >= one_lot_required:
            return 1, (
                f"Margin low: available {available_margin:.0f}, required {required:.0f} for planned {planned}; "
                f"forcing minimum 1 lot (min_margin {MIN_MARGIN_TO_TRADE:.0f})."
            )
    return eff, (
        f"Insufficient margin: available {available_margin:.0f}, required {required:.0f} for planned {planned}; "
        f"affordable {affordable}, conservative reduce by 1 -> execute {eff}."
    )


def bot_tracked_open_short_qty_by_side() -> Tuple[int, int]:
    ce_short_qty = 0
    pe_short_qty = 0
    for strategy in STRATEGY_STATE.values():
        for pos in strategy.get("positions") or []:
            if pos.get("exit_price") is not None:
                continue
            try:
                qty = int(pos.get("quantity") or 0)
            except (TypeError, ValueError):
                continue
            if qty >= 0:
                continue
            symbol = str(pos.get("symbol") or "").upper()
            if "CE" in symbol:
                ce_short_qty += abs(qty)
            elif "PE" in symbol:
                pe_short_qty += abs(qty)
    return ce_short_qty, pe_short_qty


def bot_tracked_hedge_buy_qty_by_side() -> Tuple[int, int]:
    pe_hedge_qty = 0
    ce_hedge_qty = 0
    for strategy in STRATEGY_STATE.values():
        side_qty = strategy.get("hedge_side_qty")
        if isinstance(side_qty, dict):
            try:
                pe_hedge_qty += int(side_qty.get("PE") or 0)
            except (TypeError, ValueError):
                pass
            try:
                ce_hedge_qty += int(side_qty.get("CE") or 0)
            except (TypeError, ValueError):
                pass
            continue
        for order in strategy.get("hedge_orders") or []:
            side = str(order.get("side") or "").upper()
            try:
                qty = int(order.get("quantity") or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            if side == "PE":
                pe_hedge_qty += qty
            elif side == "CE":
                ce_hedge_qty += qty
    return pe_hedge_qty, ce_hedge_qty
