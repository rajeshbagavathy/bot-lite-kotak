import copy
import datetime
from threading import Lock
from typing import Callable, Dict, Optional

# Import IST timezone helpers
from db import get_ist_now

STATE_LOCK = Lock()

# Optional: bot registers get_scheduler_diagnostics for /state merge (avoid ui importing bot).
SCHEDULER_SNAPSHOT_FN: Optional[Callable[[], dict]] = None


def set_scheduler_snapshot_fn(fn: Optional[Callable[[], dict]]) -> None:
    """Called from bot.main after jobs are scheduled."""
    global SCHEDULER_SNAPSHOT_FN
    SCHEDULER_SNAPSHOT_FN = fn


def get_scheduler_snapshot() -> Optional[dict]:
    if SCHEDULER_SNAPSHOT_FN is None:
        return None
    try:
        return SCHEDULER_SNAPSHOT_FN()
    except Exception:
        return {"error": "scheduler_snapshot_failed"}


STATE: Dict[str, dict] = {
    "index": {},
    "portfolio": {},
    "strategies": {},
    "settings": {},
    "bot": {},
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
        if "settings" not in STATE or not STATE["settings"]:
            STATE["settings"] = {"mtm_snapshots_enabled": False}
        STATE["bot"] = {"survivor_sl_to_cost_enabled": None}


def replace_strategies(strategies: Dict[str, dict]) -> None:
    """Replace live strategy map (e.g. after deferred Kotak TOTP bootstrap). Keeps portfolio/index."""
    with STATE_LOCK:
        STATE["strategies"] = strategies


def set_bot_runtime_flags(**kwargs) -> None:
    """Runtime flags from bot (e.g. config mirrors for UI)."""
    with STATE_LOCK:
        if "bot" not in STATE:
            STATE["bot"] = {}
        STATE["bot"].update(kwargs)


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


def _make_json_safe(obj):
    """Convert sets to lists so snapshot is JSON-serializable."""
    if isinstance(obj, set):
        return list(obj)
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_json_safe(v) for v in obj]
    return obj


def get_snapshot() -> dict:
    with STATE_LOCK:
        snapshot = copy.deepcopy(STATE)
    out = _make_json_safe(snapshot)
    sched = get_scheduler_snapshot()
    if sched is not None:
        out["scheduler"] = sched
    return out


def get_mtm_snapshots_enabled() -> bool:
    """Whether MTM snapshots should be written to DB (can be toggled from UI)."""
    with STATE_LOCK:
        return bool(STATE.get("settings", {}).get("mtm_snapshots_enabled", False))


def set_mtm_snapshots_enabled(enabled: bool) -> None:
    """Enable or disable MTM snapshot logging (used by UI toggle)."""
    with STATE_LOCK:
        if "settings" not in STATE:
            STATE["settings"] = {}
        STATE["settings"]["mtm_snapshots_enabled"] = bool(enabled)


def init_trading_flags(
    use_premium_based_strike: bool,
    strategy_sl_enabled: bool,
    trade_non_expiry_day: bool,
) -> None:
    """Set initial trading flags from config (called by bot on startup)."""
    with STATE_LOCK:
        if "settings" not in STATE:
            STATE["settings"] = {}
        STATE["settings"]["use_premium_based_strike"] = use_premium_based_strike
        STATE["settings"]["strategy_sl_enabled"] = strategy_sl_enabled
        STATE["settings"]["trade_non_expiry_day"] = trade_non_expiry_day


def get_trading_flag(key: str) -> Optional[bool]:
    """Return current value for a trading flag (None if not set)."""
    with STATE_LOCK:
        return STATE.get("settings", {}).get(key)


def set_trading_flag(key: str, value: bool) -> None:
    """Set a trading flag (used by UI)."""
    with STATE_LOCK:
        if "settings" not in STATE:
            STATE["settings"] = {}
        STATE["settings"][key] = bool(value)


def get_trading_flag_or(key: str, default: bool) -> bool:
    """Return trading flag value, or default if not set (for bot runtime)."""
    with STATE_LOCK:
        val = STATE.get("settings", {}).get(key)
        return bool(val) if val is not None else default


def get_all_trading_flags() -> dict:
    """Return all UI-editable flags for GET /api/settings."""
    with STATE_LOCK:
        s = STATE.get("settings") or {}
        return {
            "mtm_snapshots_enabled": bool(s.get("mtm_snapshots_enabled", False)),
            "use_premium_based_strike": bool(s.get("use_premium_based_strike", True)),
            "strategy_sl_enabled": bool(s.get("strategy_sl_enabled", False)),
            "trade_non_expiry_day": bool(s.get("trade_non_expiry_day", False)),
        }
