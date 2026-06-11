"""End-of-day square-off helpers (configurable IST wall time, bot-tagged F&O only)."""
from __future__ import annotations

import datetime
from typing import Optional, Set, Tuple

from config import EOD_SQUAREOFF_TIME, EOD_VERIFY_UNTIL
from db import get_ist_now
from trading.context import STRATEGY_STATE

_EOD_HALT: bool = False
_EOD_VERIFY_ACTIVE: bool = False
_EOD_BANNER: Optional[str] = None

_OPEN_ORDER_STATUSES = frozenset({"NEW", "REPLACED", "PENDING", "PARTIALLYFILLED"})


def _parse_hhmm(value: str) -> Tuple[int, int]:
    parts = (value or "").strip().split(":")
    if len(parts) < 2:
        return 15, 10
    try:
        return int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return 15, 10


def eod_squareoff_schedule_at() -> str:
    """``schedule`` at-time string (HH:MM)."""
    t = (EOD_SQUAREOFF_TIME or "15:10").strip()
    return t if len(t.split(":")) == 2 else t[:5]


def is_at_or_past_eod_time(now: Optional[datetime.datetime] = None) -> bool:
    t = (now or get_ist_now()).time()
    h, m = _parse_hhmm(EOD_SQUAREOFF_TIME)
    return (t.hour, t.minute) >= (h, m)


def is_at_or_past_eod_verify_until(now: Optional[datetime.datetime] = None) -> bool:
    t = (now or get_ist_now()).time()
    h, m = _parse_hhmm(EOD_VERIFY_UNTIL)
    return (t.hour, t.minute) >= (h, m)


def is_eod_halt() -> bool:
    return _EOD_HALT


def eod_verify_active() -> bool:
    return _EOD_VERIFY_ACTIVE


def eod_banner_message() -> Optional[str]:
    return _EOD_BANNER


def set_eod_banner(message: str) -> None:
    global _EOD_BANNER
    _EOD_BANNER = message


def mark_eod_halt_started(message: str) -> None:
    global _EOD_HALT, _EOD_VERIFY_ACTIVE
    _EOD_HALT = True
    _EOD_VERIFY_ACTIVE = True
    set_eod_banner(message)


def mark_eod_verify_complete(message: str) -> None:
    global _EOD_VERIFY_ACTIVE
    _EOD_VERIFY_ACTIVE = False
    set_eod_banner(message)


def reset_eod_state() -> None:
    """Test helper: clear in-process EOD flags."""
    global _EOD_HALT, _EOD_VERIFY_ACTIVE, _EOD_BANNER
    _EOD_HALT = False
    _EOD_VERIFY_ACTIVE = False
    _EOD_BANNER = None


def collect_bot_tracked_instrument_ids() -> Set[int]:
    """Instrument IDs the bot opened today (straddle legs, hedges, SL map)."""
    ids: Set[int] = set()
    for strategy in STRATEGY_STATE.values():
        for raw in strategy.get("instrument_ids") or []:
            try:
                ids.add(int(raw))
            except (TypeError, ValueError):
                continue
        for pos in strategy.get("positions") or []:
            if pos.get("exit_price") is not None:
                continue
            try:
                iid = int(pos.get("instrument_id") or 0)
            except (TypeError, ValueError):
                continue
            if iid:
                ids.add(iid)
        for iid in (strategy.get("sl_tag_map") or {}).values():
            try:
                ids.add(int(iid))
            except (TypeError, ValueError):
                continue
        for order in strategy.get("hedge_orders") or []:
            try:
                iid = int(order.get("instrument_id") or 0)
            except (TypeError, ValueError):
                continue
            if iid:
                ids.add(iid)
    return ids


def is_bot_sl_order_tag(tag: str) -> bool:
    tag_s = str(tag or "")
    if not tag_s:
        return False
    for name in STRATEGY_STATE:
        if tag_s.startswith(f"{name}_SL_"):
            return True
    return False


def is_cancellable_order_status(status: str) -> bool:
    return str(status or "").upper() in _OPEN_ORDER_STATUSES
