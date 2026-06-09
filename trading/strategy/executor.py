"""Strategy entry execution — calm → strikes → margin → orders → SL protection."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, List

from config import (
    CALM_ZONE_GATEKEEPER_POLL_SECONDS,
    CALM_ZONE_WAIT_TIMEOUT_MINUTES,
    ITM_STRIKES_NIFTY,
    ITM_STRIKES_SENSEX,
    LEG_TARGET_PCT,
    STRIKE_PREMIUM_BUFFER_NIFTY,
    STRIKE_PREMIUM_BUFFER_SENSEX,
    STRIKE_PREMIUM_TARGET_NIFTY,
    STRIKE_PREMIUM_TARGET_SENSEX,
    USE_PREMIUM_BASED_STRIKE,
)
from db import get_ist_now, get_ist_timestamp, log_order, log_strategy_execution, upsert_strategy_waiting_for_calm
from state import get_trading_flag_or
from trading.compat import resolve
from trading.context import STRATEGY_STATE
from trading.journal import Phase, record as journal
from trading.orders.close import cancel_order_logged, close_positions_for_instruments
from trading.orders.lifecycle import complete_entry_with_sl_protection
from trading.strategy.gatekeeper import (
    calm_gatekeeper_context_blurb,
    gatekeeper_window_start_iso,
    should_execute_now as _should_execute_now,
)
from trading.strategy.margin import ensure_margin_or_skip_strategy as _ensure_margin_or_skip_strategy
from trading.strategy.strikes import find_strike_by_premium
from trading.state_bridge import update_strategy
from trading.utils import get_atm_strike as _get_atm_strike, is_expiry_day

logger = logging.getLogger("xts-bot-lite")

_exec_locks: dict[str, threading.Lock] = {}
_exec_locks_guard = threading.Lock()


def _strategy_exec_lock(name: str) -> threading.Lock:
    with _exec_locks_guard:
        if name not in _exec_locks:
            _exec_locks[name] = threading.Lock()
        return _exec_locks[name]


def execute_strategy(client: Any, index_config, expiry: str, strategy, force: bool = False) -> None:
    if strategy["status"] not in ("PENDING", "ERROR", "WAITING_FOR_CALM"):
        return
    if not force and get_ist_now().strftime("%H:%M:%S") < strategy["time"]:
        return
    name = strategy["name"]
    lock = _strategy_exec_lock(name)
    if not lock.acquire(blocking=False):
        journal(
            Phase.CRITERIA_FAILED,
            name,
            "Entry already in progress for this strategy — skipped duplicate run",
            severity="WARNING",
        )
        return
    try:
        _execute_strategy_locked(client, index_config, expiry, strategy, force=force)
    except Exception as exc:
        logger.exception("[%s] Entry pipeline failed", name)
        journal(
            Phase.STRATEGY_ABORT,
            name,
            f"Entry pipeline error: {exc}",
            severity="ERROR",
        )
        update_strategy(name, status="ERROR", message=f"Entry pipeline error: {exc}"[:200])
    finally:
        lock.release()


def _execute_strategy_locked(client: Any, index_config, expiry: str, strategy, force: bool = False) -> None:
    name = strategy["name"]
    if not force and strategy.get("status") == "PENDING":
        journal(
            Phase.STRATEGY_SLOTTED,
            name,
            f"Slot {strategy.get('time')} reached — starting entry pipeline",
            slot_time=strategy.get("time"),
            planned_lots=strategy.get("lots"),
        )

    skip_calm_recheck = force and strategy.get("status") == "WAITING_FOR_CALM"
    if skip_calm_recheck:
        can_run, gate_reason, gate_row = True, "calm_gatekeeper", None
    else:
        can_run, gate_reason, gate_row = resolve("should_execute_now", _should_execute_now)(name, index_config.name)
    if not can_run:
        now_ts = get_ist_now().timestamp()
        gk_started = strategy.get("gatekeeper_started_at") or gatekeeper_window_start_iso(strategy)
        wait_msg = (
            f"Waiting for calm zone ({gate_reason}; {calm_gatekeeper_context_blurb(gate_row)})"
        )
        was_waiting = strategy.get("status") == "WAITING_FOR_CALM"
        update_strategy(
            name,
            status="WAITING_FOR_CALM",
            gatekeeper_started_at=gk_started,
            next_gatekeeper_check_at=now_ts + float(CALM_ZONE_GATEKEEPER_POLL_SECONDS),
            message=wait_msg,
        )
        if not was_waiting:
            journal(
                Phase.WAITING_FOR_CALM,
                name,
                wait_msg,
                severity="WARNING",
                gate_reason=gate_reason,
                bar=gate_row,
                timeout_min=CALM_ZONE_WAIT_TIMEOUT_MINUTES,
            )
        elif was_waiting:
            journal(
                Phase.CRITERIA_FAILED,
                name,
                f"Calm no longer valid ({gate_reason}); resuming wait",
                severity="WARNING",
                gate_reason=gate_reason,
                bar=gate_row,
            )
        sid = upsert_strategy_waiting_for_calm(
            name,
            int(strategy.get("lots") or 0),
            float(strategy.get("leg_sl_pct") or 0.0),
            float(strategy.get("strategy_sl") or 0.0),
            gk_started,
            int(strategy["db_id"]) if strategy.get("db_id") else None,
        )
        if sid > 0:
            update_strategy(name, db_id=sid)
        return

    if strategy.get("status") == "WAITING_FOR_CALM":
        update_strategy(
            name,
            status="ENTERING",
            message="Calm zone confirmed; running entry pipeline (strikes → margin → orders)",
            next_gatekeeper_check_at=get_ist_now().timestamp() + 600,
        )
    elif gate_reason not in ("gatekeeper_disabled",):
        journal(
            Phase.CALM_PASSED,
            name,
            f"Gatekeeper passed ({gate_reason})",
            gate_reason=gate_reason,
            bar=gate_row,
        )

    is_expiry = is_expiry_day(expiry)
    warm_chain = getattr(client, "warm_option_chain", None)
    if callable(warm_chain):
        try:
            chain_rows = warm_chain(index_config, expiry)
            journal(
                Phase.CRITERIA_CHECK,
                name,
                f"Option chain loaded ({chain_rows} scrip rows)",
                expiry=expiry,
                chain_rows=chain_rows,
            )
        except Exception as exc:
            logger.warning("[%s] Option chain preload failed: %s", name, exc)
    atm_strike = resolve("_get_atm_strike", _get_atm_strike)(client, index_config)
    if atm_strike is None:
        journal(Phase.CRITERIA_FAILED, name, "Spot LTP unavailable", severity="ERROR")
        update_strategy(name, status="ERROR", message="Spot LTP unavailable")
        return

    strike_diff = int(index_config.strike_diff)
    use_premium_strike = get_trading_flag_or("use_premium_based_strike", USE_PREMIUM_BASED_STRIKE) and not is_expiry
    if use_premium_strike:
        if index_config.name == "NIFTY":
            target, buffer = STRIKE_PREMIUM_TARGET_NIFTY, STRIKE_PREMIUM_BUFFER_NIFTY
        else:
            target, buffer = STRIKE_PREMIUM_TARGET_SENSEX, STRIKE_PREMIUM_BUFFER_SENSEX
        min_p, max_p = target - buffer, target + buffer
        ce_result = find_strike_by_premium(client, index_config, expiry, "CE", atm_strike, target, min_p, max_p)
        pe_result = find_strike_by_premium(client, index_config, expiry, "PE", atm_strike, target, min_p, max_p)
        if ce_result is None or pe_result is None:
            journal(
                Phase.CRITERIA_FAILED,
                name,
                f"Strike not in premium band ₹{min_p:.0f}–₹{max_p:.0f}",
                severity="WARNING",
                ce_in_range=ce_result is not None,
                pe_in_range=pe_result is not None,
                atm=atm_strike,
            )
            now_ts = get_ist_now().timestamp()
            gk_started = strategy.get("gatekeeper_started_at") or gatekeeper_window_start_iso(strategy)
            update_strategy(
                name,
                status="WAITING_FOR_CALM",
                gatekeeper_started_at=gk_started,
                next_gatekeeper_check_at=now_ts + float(CALM_ZONE_GATEKEEPER_POLL_SECONDS),
                message=f"Strike not in premium range; retrying within calm window",
            )
            return
        ce_strike, ce_id = ce_result
        pe_strike, pe_id = pe_result
    else:
        if is_expiry:
            n = int(ITM_STRIKES_SENSEX) if index_config.name == "SENSEX" else int(ITM_STRIKES_NIFTY)
            ce_strike = atm_strike - n * strike_diff
            pe_strike = atm_strike + n * strike_diff
        else:
            ce_strike = pe_strike = atm_strike
        ce_id = client.get_option_instrument_id(index_config, expiry, "CE", ce_strike)
        pe_id = client.get_option_instrument_id(index_config, expiry, "PE", pe_strike)
        if not ce_id or not pe_id:
            journal(
                Phase.CRITERIA_FAILED,
                name,
                f"Instruments not found CE={ce_strike} PE={pe_strike}",
                severity="ERROR",
            )
            update_strategy(name, status="ERROR", message="Option instruments not found")
            return

    journal(
        Phase.STRIKE_SELECTED,
        name,
        f"Strikes CE={ce_strike} PE={pe_strike} ATM={atm_strike}",
        ce_strike=ce_strike,
        pe_strike=pe_strike,
        ce_id=ce_id,
        pe_id=pe_id,
        atm_strike=atm_strike,
        expiry=expiry,
        is_expiry=is_expiry,
    )

    if not resolve("_ensure_margin_or_skip_strategy", _ensure_margin_or_skip_strategy)(
        client, index_config, expiry, strategy, atm_strike
    ):
        st = STRATEGY_STATE.get(name, {})
        journal(
            Phase.CRITERIA_FAILED,
            name,
            st.get("message") or "Margin gate blocked entry",
            severity="ERROR",
        )
        return

    effective_lots = int(strategy["lots"])
    effective_leg_sl_pct = float(strategy["leg_sl_pct"])
    qty = effective_lots * index_config.lot_size
    tag_ts = int(time.time())
    ce_tag = f"{name}_CE_SELL_{tag_ts}"
    pe_tag = f"{name}_PE_SELL_{tag_ts}"

    entry_ltps = client.get_ltp_map(
        [
            {"exchangeSegment": index_config.option_ltp_segment, "exchangeInstrumentID": int(ce_id)},
            {"exchangeSegment": index_config.option_ltp_segment, "exchangeInstrumentID": int(pe_id)},
        ]
    )

    placed_entry: List[dict] = []
    for instrument_id, tag in ((ce_id, ce_tag), (pe_id, pe_tag)):
        order_id = client.place_market_order(
            index_config=index_config,
            instrument_id=instrument_id,
            order_side=client.interactive.TRANSACTION_TYPE_SELL,
            quantity=qty,
            tag=tag,
            product_type=client.interactive.PRODUCT_MIS,
            ltp=entry_ltps.get(int(instrument_id)),
        )
        if order_id:
            placed_entry.append({"app_order_id": order_id, "tag": tag, "instrument_id": int(instrument_id), "quantity": qty})
        else:
            for placed in placed_entry:
                cancel_order_logged(client, name, placed["app_order_id"], placed["tag"], flow="entry_rollback")
            try:
                positions = client.get_positions()
                close_positions_for_instruments(
                    client, index_config, positions, [p["instrument_id"] for p in placed_entry],
                    strategy_name=name, flow="entry_rollback",
                )
            except Exception:
                logger.exception("Rollback failed for %s", name)
            journal(Phase.CRITERIA_FAILED, name, "Entry order placement failed", severity="ERROR", ce_id=ce_id, pe_id=pe_id)
            update_strategy(name, status="ERROR", message="Entry order placement failed")
            return

    for placed in placed_entry:
        log_order(name, int(placed["app_order_id"]), str(placed["tag"]), int(placed["instrument_id"]), "", qty, "LIMIT", "SELL")

    update_strategy(
        name,
        status="OPEN",
        strike=atm_strike,
        strike_ce=ce_strike,
        strike_pe=pe_strike,
        instrument_ids=[ce_id, pe_id],
        entry_time=get_ist_timestamp(),
        order_tags=[ce_tag, pe_tag],
    )

    existing_id = strategy.get("db_id")
    strategy_db_id = log_strategy_execution(
        name, atm_strike, get_ist_timestamp(), effective_lots, effective_leg_sl_pct,
        strategy.get("strategy_sl", 0.0), existing_db_id=int(existing_id) if existing_id else None,
    )
    update_strategy(name, db_id=strategy_db_id)

    journal(
        Phase.ENTRY_SENT,
        name,
        f"Entry orders placed ({len(placed_entry)} legs, {effective_lots} lots)",
        entry_order_ids=[p.get("app_order_id") for p in placed_entry],
        lots=effective_lots,
        qty=qty,
        ce_strike=ce_strike,
        pe_strike=pe_strike,
    )

    protection = complete_entry_with_sl_protection(
        client, index_config, strategy, placed_entry, effective_leg_sl_pct, LEG_TARGET_PCT,
    )
    if not protection.ok:
        return
    update_strategy(
        name,
        sl_orders=protection.sl_orders,
        positions=protection.positions,
        sl_tag_map=protection.sl_tag_map,
    )
