"""
Structured per-strategy trade journal — one JSON line per lifecycle event.

Tail on EC2:
  jq -r '[.ts,.phase,.strategy,.message] | @tsv' trade_journal.jsonl
  jq 'select(.strategy=="X_H_1231")' trade_journal.jsonl
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from db import get_ist_now

_log = logging.getLogger("xts-bot-lite.journal")
_lock = threading.Lock()
_journal_path: Optional[str] = None


class Phase(str, Enum):
    """Strategy lifecycle phases (ordered roughly by typical flow)."""

    # Scheduling / calm
    STRATEGY_SLOTTED = "STRATEGY_SLOTTED"
    WAITING_FOR_CALM = "WAITING_FOR_CALM"
    CALM_CHECK = "CALM_CHECK"
    CALM_PASSED = "CALM_PASSED"
    SKIPPED_VOLATILITY = "SKIPPED_VOLATILITY"
    STRATEGY_DISABLED = "STRATEGY_DISABLED"

    # Pre-entry criteria
    CRITERIA_CHECK = "CRITERIA_CHECK"
    CRITERIA_FAILED = "CRITERIA_FAILED"
    STRIKE_SELECTED = "STRIKE_SELECTED"
    MARGIN_CHECK = "MARGIN_CHECK"
    HEDGE_PLACED = "HEDGE_PLACED"
    HEDGE_SEARCH = "HEDGE_SEARCH"
    LOTS_SIZED = "LOTS_SIZED"

    # Entry + SL
    ENTRY_SENT = "ENTRY_SENT"
    ENTRY_FILL_WAIT = "ENTRY_FILL_WAIT"
    ENTRY_FILLED = "ENTRY_FILLED"
    ENTRY_FILL_TIMEOUT = "ENTRY_FILL_TIMEOUT"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    SL_SENT = "SL_SENT"
    SL_VERIFY = "SL_VERIFY"
    PROTECTED = "PROTECTED"
    SAFETY_FLATTEN = "SAFETY_FLATTEN"
    SL_REJECTED = "SL_REJECTED"
    SL_MISSING = "SL_MISSING"

    # Open / monitor
    MONITOR_INVARIANT = "MONITOR_INVARIANT"
    LEG_TARGET_HIT = "LEG_TARGET_HIT"
    SL_FILLED = "SL_FILLED"
    SURVIVOR_SL_TO_COST = "SURVIVOR_SL_TO_COST"
    POSITION_SYNC = "POSITION_SYNC"

    # Close
    STRATEGY_ABORT = "STRATEGY_ABORT"
    STRATEGY_CLOSE = "STRATEGY_CLOSE"
    RESTORE_SL_LINKS = "RESTORE_SL_LINKS"


@dataclass
class JournalEvent:
    phase: str
    strategy: str
    message: str
    ts: str = field(default_factory=lambda: get_ist_now().isoformat(timespec="seconds"))
    severity: str = "INFO"
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def init_journal(log_path: Optional[str] = None) -> str:
    """Call once at startup. Returns absolute journal file path."""
    global _journal_path
    if log_path:
        _journal_path = os.path.abspath(log_path)
    else:
        env = os.environ.get("TRADE_JOURNAL_PATH")
        if env:
            _journal_path = os.path.abspath(env)
        else:
            bot_log = os.environ.get("BOT_LOG_PATH")
            if bot_log:
                base = os.path.dirname(os.path.abspath(bot_log))
            else:
                base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            _journal_path = os.path.join(base, "trade_journal.jsonl")
    os.environ["TRADE_JOURNAL_PATH"] = _journal_path
    _log.info("Trade journal: %s", _journal_path)
    record("SYSTEM", "", "Bot journal initialized", severity="INFO", path=_journal_path)
    return _journal_path


def journal_path() -> str:
    if _journal_path is None:
        return init_journal()
    return _journal_path


def record(
    phase: Phase | str,
    strategy: str,
    message: str,
    *,
    severity: str = "INFO",
    **details: Any,
) -> None:
    """Append one journal line and mirror summary to bot.log."""
    phase_str = phase.value if isinstance(phase, Phase) else str(phase)
    ev = JournalEvent(
        phase=phase_str,
        strategy=strategy or "",
        message=message,
        severity=severity,
        details=details,
    )
    line = json.dumps(ev.to_dict(), default=str, ensure_ascii=False)
    path = journal_path()

    with _lock:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as e:
            _log.error("Could not write journal %s: %s", path, e)

    log_fn = _log.warning if severity in ("WARNING", "ERROR", "CRITICAL") else _log.info
    detail_suffix = ""
    if details:
        compact = json.dumps(details, default=str, ensure_ascii=False)
        if len(compact) > 500:
            compact = compact[:497] + "..."
        detail_suffix = f" | {compact}"
    label = strategy or "SYSTEM"
    log_fn("[%s] %s — %s%s", phase_str, label, message, detail_suffix)


def read_tail(n: int = 50, *, strategy: Optional[str] = None) -> list:
    """Return last *n* parsed journal events (newest at end of list)."""
    path = journal_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    out = []
    for raw in lines[-n:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if strategy is not None and str(ev.get("strategy") or "") != strategy:
            continue
        out.append(ev)
    return out
