import copy
import datetime
from threading import Lock
from typing import Dict, Optional

# Import IST timezone helpers
from db import get_ist_now

STATE_LOCK = Lock()
STATE: Dict[str, dict] = {
    "index": {},
    "portfolio": {},
    "strategies": {},
}


def init_state(strategies: Dict[str, dict]) -> None:
    with STATE_LOCK:
        STATE["index"] = {"name": None, "expiry": None, "spot": None, "error": None}
        STATE["portfolio"] = {
            "mtm": 0.0,
            "realized": 0.0,
            "unrealized": 0.0,
            "sl_limit": None,
            "available_margin": None,
            "last_update": None,
            "margin_update": None,
        }
        STATE["strategies"] = strategies


def set_index(name: str, expiry: str) -> None:
    with STATE_LOCK:
        STATE["index"]["name"] = name
        STATE["index"]["expiry"] = expiry
        STATE["index"]["error"] = None  # Clear error on successful set


def set_index_error(error_message: str) -> None:
    """Set an error message when expiry cannot be found."""
    with STATE_LOCK:
        STATE["index"]["error"] = error_message
        STATE["index"]["name"] = None
        STATE["index"]["expiry"] = None


def set_spot(spot: float) -> None:
    with STATE_LOCK:
        STATE["index"]["spot"] = spot


def update_portfolio(mtm: float, realized: float, unrealized: float, sl_limit: float) -> None:
    with STATE_LOCK:
        STATE["portfolio"]["mtm"] = mtm
        STATE["portfolio"]["realized"] = realized
        STATE["portfolio"]["unrealized"] = unrealized
        STATE["portfolio"]["sl_limit"] = sl_limit
        STATE["portfolio"]["last_update"] = get_ist_now().isoformat(timespec="seconds")


def update_portfolio_margin(available_margin: Optional[float]) -> None:
    with STATE_LOCK:
        STATE["portfolio"]["available_margin"] = available_margin
        STATE["portfolio"]["margin_update"] = get_ist_now().isoformat(timespec="seconds")


def update_strategy(name: str, **fields) -> None:
    with STATE_LOCK:
        strategy = STATE["strategies"].get(name)
        if strategy is None:
            return
        strategy.update(fields)
        strategy["last_update"] = get_ist_now().isoformat(timespec="seconds")


def get_snapshot() -> dict:
    with STATE_LOCK:
        return copy.deepcopy(STATE)
