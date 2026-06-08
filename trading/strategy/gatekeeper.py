"""Calm-zone gatekeeper and entry window helpers."""
from __future__ import annotations

import datetime
import logging
import time
from typing import Any, Optional, Tuple

from config import (
    CALM_ZONE_GATEKEEPER_MODE,
    CALM_ZONE_GATEKEEPER_POLL_SECONDS,
    CALM_ZONE_RECENT_CALM_MINUTES,
    CALM_ZONE_WAIT_TIMEOUT_MINUTES,
    USE_CALM_ZONE_GATEKEEPER,
)
from db import (
    fetch_last_two_spot_bar_rows,
    fetch_latest_spot_bar_row,
    fetch_recent_calm_spot_row,
    get_ist_now,
    mark_strategy_skipped_volatility_db,
    upsert_strategy_waiting_for_calm,
)
from trading.compat import resolve
from trading.context import STRATEGY_STATE
from trading.journal import Phase, record as journal
from trading.state_bridge import update_strategy

logger = logging.getLogger("xts-bot-lite")


def calm_gatekeeper_context_blurb(row: Optional[dict]) -> str:
    if not row:
        return "latest=none"
    parts = [f"latest={row.get('bar_time')}"]
    pt = row.get("prior_bar_time")
    if pt:
        parts.append(f"prior={pt}")
    if row.get("range_5m") is not None:
        parts.append(f"range={row.get('range_5m')}")
    if row.get("body_range_ratio") is not None:
        parts.append(f"ratio={row.get('body_range_ratio')}")
    return ", ".join(parts)


def spot_row_is_calm(row: Optional[dict], index_name: str) -> bool:
    if not row:
        return False
    try:
        rg = row.get("range_5m")
        rt = row.get("body_range_ratio")
        if rg is not None and rt is not None:
            thr = 50.0 if str(index_name or "").upper() == "NIFTY" else 120.0
            return float(rg) < thr and float(rt) < 0.25
    except (TypeError, ValueError):
        pass
    return bool(int(row.get("is_calmzone") or 0))


def should_execute_now(strategy_id: str, index_name: str) -> Tuple[bool, str, Optional[dict]]:
    if not resolve("USE_CALM_ZONE_GATEKEEPER", USE_CALM_ZONE_GATEKEEPER):
        return True, "gatekeeper_disabled", None
    mode = (resolve("CALM_ZONE_GATEKEEPER_MODE", CALM_ZONE_GATEKEEPER_MODE) or "current_or_prior_calm").strip().lower()
    if mode not in ("latest_bar", "current_or_prior_calm", "recent_calm"):
        mode = "current_or_prior_calm"

    if mode == "latest_bar":
        latest = resolve("fetch_latest_spot_bar_row", fetch_latest_spot_bar_row)(index_name)
        if not latest:
            return False, "no_data", None
        is_calm = spot_row_is_calm(latest, index_name)
        return (True, "calm", latest) if is_calm else (False, "volatile", latest)

    if mode == "current_or_prior_calm":
        rows = resolve("fetch_last_two_spot_bar_rows", fetch_last_two_spot_bar_rows)(index_name)
        if not rows:
            return False, "no_data", None
        latest = rows[0]
        prior = rows[1] if len(rows) > 1 else None
        if spot_row_is_calm(latest, index_name):
            return True, "calm_current", latest
        if prior and spot_row_is_calm(prior, index_name):
            return True, "calm_prior", prior
        ctx = dict(latest)
        if prior:
            ctx["prior_bar_time"] = prior.get("bar_time")
        return False, "volatile", ctx

    latest = resolve("fetch_latest_spot_bar_row", fetch_latest_spot_bar_row)(index_name)
    recent_min = resolve("CALM_ZONE_RECENT_CALM_MINUTES", CALM_ZONE_RECENT_CALM_MINUTES)
    min_u = int(time.time()) - int(recent_min) * 60
    calm_row = resolve("fetch_recent_calm_spot_row", fetch_recent_calm_spot_row)(index_name, min_u)
    if calm_row:
        return True, "calm_recent", calm_row
    if not latest:
        return False, "no_data", None
    return False, "volatile", latest


def gatekeeper_window_start_iso(strategy: dict) -> str:
    slot_dt, _ = strategy_entry_window(strategy)
    return slot_dt.isoformat(timespec="seconds")


def normalize_strategy_time_hhmmss(raw: str) -> Optional[str]:
    if not raw:
        return None
    parts = str(raw).strip().split(":")
    try:
        h, m = int(parts[0]), int(parts[1])
        s = int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        return None
    return f"{h:02d}:{m:02d}:{s:02d}"


def strategy_slot_ist_datetime(strategy: dict) -> datetime.datetime:
    t = normalize_strategy_time_hhmmss(strategy.get("time") or "") or "09:15:00"
    now = get_ist_now()
    h, m, s = (int(x) for x in t.split(":"))
    return now.replace(hour=h, minute=m, second=s, microsecond=0)


def strategy_entry_window(strategy: dict, now: Optional[datetime.datetime] = None):
    now = now or get_ist_now()
    slot_dt = strategy_slot_ist_datetime(strategy)
    window_end = slot_dt + datetime.timedelta(minutes=int(CALM_ZONE_WAIT_TIMEOUT_MINUTES))
    return slot_dt, window_end


def process_waiting_for_calm(client: Any, index_config, expiry: str, execute_fn) -> None:
    """Poll WAITING_FOR_CALM strategies; *execute_fn* is ``execute_strategy``."""
    now = get_ist_now()
    now_ts = now.timestamp()
    for strategy in STRATEGY_STATE.values():
        if strategy.get("status") != "WAITING_FOR_CALM":
            continue
        name = strategy["name"]
        started_at = strategy.get("gatekeeper_started_at")
        if not started_at:
            started_at = now.isoformat(timespec="seconds")
            update_strategy(name, gatekeeper_started_at=started_at)
        try:
            started_dt = datetime.datetime.fromisoformat(str(started_at))
        except ValueError:
            started_dt = now
        elapsed_min = (now - started_dt).total_seconds() / 60.0
        if elapsed_min > float(CALM_ZONE_WAIT_TIMEOUT_MINUTES):
            msg = f"SKIPPED: No calm zone within {CALM_ZONE_WAIT_TIMEOUT_MINUTES}m window"
            journal(Phase.SKIPPED_VOLATILITY, name, msg, severity="WARNING", elapsed_min=round(elapsed_min, 1))
            update_strategy(name, status="SKIPPED_VOLATILITY", message=msg, skip_reason="NO_CALM_ZONE_TIMEOUT")
            db_id = strategy.get("db_id")
            if db_id:
                mark_strategy_skipped_volatility_db(int(db_id), name, "NO_CALM_ZONE_TIMEOUT")
            continue
        next_check_at = strategy.get("next_gatekeeper_check_at") or 0
        try:
            next_check_at_f = float(next_check_at)
        except (TypeError, ValueError):
            next_check_at_f = 0.0
        if now_ts < next_check_at_f:
            continue
        can_run, reason, row = resolve("should_execute_now", should_execute_now)(name, index_config.name)
        journal(
            Phase.CALM_CHECK,
            name,
            f"Calm poll: {'PASS' if can_run else 'WAIT'} ({reason})",
            gate_reason=reason,
            bar_context=calm_gatekeeper_context_blurb(row),
            elapsed_min=round(elapsed_min, 1),
        )
        if can_run:
            update_strategy(name, message=f"Calm Zone detected ({reason}); executing.")
            journal(Phase.CALM_PASSED, name, f"Calm zone passed ({reason})", gate_reason=reason, bar=row)
            execute_fn(client, index_config, expiry, strategy, force=True)
        else:
            update_strategy(
                name,
                next_gatekeeper_check_at=now_ts + float(CALM_ZONE_GATEKEEPER_POLL_SECONDS),
                message=f"Waiting for Calm Zone ({reason}; {calm_gatekeeper_context_blurb(row)})",
            )
