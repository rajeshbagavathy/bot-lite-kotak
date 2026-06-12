"""End-of-day square-off helpers (configurable IST wall time, bot-tagged F&O only)."""
from __future__ import annotations

import datetime
import re
import time
from typing import Dict, Optional, Set, Tuple

from config import EOD_CLOSE_STALE_RETRY_SEC, EOD_SQUAREOFF_TIME, EOD_VERIFY_UNTIL
from db import get_ist_now
from trading.context import STRATEGY_STATE

_EOD_HALT: bool = False
_EOD_VERIFY_ACTIVE: bool = False
_EOD_BANNER: Optional[str] = None
_EOD_CLOSE_PENDING: Dict[int, dict] = {}
_EOD_BROKER_OPEN_POSITIONS: int = 0
_EOD_BROKER_OPEN_SL: int = 0

_OPEN_ORDER_STATUSES = frozenset(
    {"NEW", "REPLACED", "PENDING", "PARTIALLYFILLED", "OPEN", "TRIGGERPENDING"}
)


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
    """True at/after EOD_VERIFY_UNTIL (default 15:19 IST) — hard stop for retry loop."""
    t = (now or get_ist_now()).time()
    h, m = _parse_hhmm(EOD_VERIFY_UNTIL)
    return (t.hour, t.minute) >= (h, m)


def is_within_eod_retry_window(now: Optional[datetime.datetime] = None) -> bool:
    """True between EOD_SQUAREOFF_TIME (inclusive) and EOD_VERIFY_UNTIL (exclusive)."""
    t = (now or get_ist_now()).time()
    key = (t.hour, t.minute)
    sh, sm = _parse_hhmm(EOD_SQUAREOFF_TIME)
    eh, em = _parse_hhmm(EOD_VERIFY_UNTIL)
    return (sh, sm) <= key < (eh, em)


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
    global _EOD_HALT, _EOD_VERIFY_ACTIVE, _EOD_BANNER, _EOD_CLOSE_PENDING
    global _EOD_BROKER_OPEN_POSITIONS, _EOD_BROKER_OPEN_SL
    _EOD_HALT = False
    _EOD_VERIFY_ACTIVE = False
    _EOD_BANNER = None
    _EOD_CLOSE_PENDING = {}
    _EOD_BROKER_OPEN_POSITIONS = 0
    _EOD_BROKER_OPEN_SL = 0


def eod_broker_exposure_counts() -> Tuple[int, int]:
    """Last counted open broker positions / SL orders during EOD verify."""
    return _EOD_BROKER_OPEN_POSITIONS, _EOD_BROKER_OPEN_SL


def set_eod_broker_exposure_counts(open_positions: int, open_sl: int) -> None:
    global _EOD_BROKER_OPEN_POSITIONS, _EOD_BROKER_OPEN_SL
    _EOD_BROKER_OPEN_POSITIONS = int(open_positions)
    _EOD_BROKER_OPEN_SL = int(open_sl)


def should_place_eod_close(instrument_id: int, signed_qty: int) -> bool:
    """Skip duplicate EOD close while broker still shows the same signed exposure (pending fill)."""
    if signed_qty == 0:
        return False
    prev = _EOD_CLOSE_PENDING.get(int(instrument_id))
    if not prev:
        return True
    if int(prev.get("signed_qty") or 0) != int(signed_qty):
        return True
    placed_at = float(prev.get("placed_at") or 0)
    return (time.time() - placed_at) >= float(EOD_CLOSE_STALE_RETRY_SEC)


def record_eod_close_placed(instrument_id: int, signed_qty: int) -> None:
    _EOD_CLOSE_PENDING[int(instrument_id)] = {
        "signed_qty": int(signed_qty),
        "placed_at": time.time(),
    }


def note_eod_position_flat(instrument_id: int) -> None:
    _EOD_CLOSE_PENDING.pop(int(instrument_id), None)


def collect_bot_tracked_instrument_ids() -> Set[int]:
    """Instrument IDs the bot opened today (straddle legs, hedges, SL map, DB registry)."""
    ids: Set[int] = set()
    try:
        from db import load_bot_tracked_instrument_ids_for_today

        ids.update(load_bot_tracked_instrument_ids_for_today())
    except Exception:
        pass
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


_BOT_SL_TAG_RE = re.compile(r"^[A-Za-z0-9_]+_SL_\d+$")


def is_bot_sl_order_tag(tag: str) -> bool:
    tag_s = str(tag or "")
    if not tag_s:
        return False
    for name in STRATEGY_STATE:
        if tag_s.startswith(f"{name}_SL_"):
            return True
    if STRATEGY_STATE:
        return False
    if tag_s.upper().startswith("MANUAL"):
        return False
    return bool(_BOT_SL_TAG_RE.match(tag_s))


def is_cancellable_order_status(status: str) -> bool:
    key = str(status or "").replace(" ", "").replace("_", "").upper()
    return key in _OPEN_ORDER_STATUSES


def count_remaining_bot_exposure(
    broker_positions: list,
    order_book: list,
    *,
    eod_verify: bool = False,
) -> Tuple[int, int]:
    """
    Count open bot positions and cancellable SL orders still on the book.
    During EOD verify, count all non-zero broker positions (not only in-memory IDs).
    """
    bot_ids = collect_bot_tracked_instrument_ids()
    count_all_positions = bool(eod_verify) or not bot_ids
    open_positions = 0
    for pos in broker_positions or []:
        try:
            iid = int(pos.get("ExchangeInstrumentId") or 0)
            qty = int(pos.get("Quantity") or 0)
        except (TypeError, ValueError):
            continue
        if qty == 0:
            continue
        if not count_all_positions and iid not in bot_ids:
            continue
        open_positions += 1
    open_sl = 0
    for order in order_book or []:
        tag = str(order.get("OrderUniqueIdentifier") or "")
        if not is_bot_sl_order_tag(tag):
            continue
        if is_cancellable_order_status(str(order.get("OrderStatus") or "")):
            open_sl += 1
    return open_positions, open_sl
