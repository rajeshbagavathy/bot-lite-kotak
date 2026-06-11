#!/usr/bin/env python3
"""
Emergency square-off: market-close all open F&O positions and cancel open bot SL orders.

Usage (fresh TOTP required if bot is not already logged in on this process):
  python scripts/force_squareoff.py --totp 123456

Dry-run (no broker calls):
  python scripts/force_squareoff.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Force market square-off + cancel bot SL orders")
    parser.add_argument("--totp", metavar="CODE", help="Current 6-digit Kotak TOTP")
    parser.add_argument("--dry-run", action="store_true", help="Print actions only")
    args = parser.parse_args()

    totp_code = None
    if args.totp:
        totp_code = str(args.totp).strip().replace(" ", "")
        if not totp_code.isdigit() or len(totp_code) != 6:
            print("error: --totp must be exactly 6 digits", file=sys.stderr)
            return 1
        os.environ["KOTAK_TOTP"] = totp_code
        os.environ["KOTAK_TOTP_UI"] = "false"

    from brokers.factory import create_trading_client
    from config import DEMO_MODE

    if DEMO_MODE:
        print("DEMO_MODE is enabled — cannot place real orders", file=sys.stderr)
        return 1

    client = create_trading_client()
    if totp_code:
        client.login_with_totp(totp_code)
    else:
        client.login()

    import bot
    from bot import (
        _cancel_bot_open_sl_orders,
        _pick_index_and_expiry,
        _place_close_order,
        _load_strategy_state,
    )
    from trading.eod import (
        collect_bot_tracked_instrument_ids,
        is_bot_sl_order_tag,
        is_cancellable_order_status,
        reset_eod_state,
    )

    index_config, expiry = _pick_index_and_expiry(client)
    print(f"Index: {index_config.name}  Expiry: {expiry}")

    bot.STRATEGY_STATE.clear()
    _load_strategy_state(client, index_config, expiry=expiry)
    if callable(getattr(client, "warm_option_chain", None)):
        try:
            client.warm_option_chain(index_config, expiry)
        except Exception as e:
            print(f"warn: option chain warm failed: {e}")

    positions = client.get_positions()
    order_book = client.get_order_book()
    bot_ids = collect_bot_tracked_instrument_ids()
    use_all = not bot_ids
    if use_all:
        print("warn: no bot-tracked instrument IDs in state — closing ALL non-zero positions")

    closed = 0
    for pos in positions or []:
        try:
            qty = int(pos.get("Quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty == 0:
            continue
        try:
            iid = int(pos.get("ExchangeInstrumentId") or 0)
        except (TypeError, ValueError):
            iid = 0
        if not use_all and iid not in bot_ids:
            continue
        sym = pos.get("TradingSymbol") or iid
        print(f"CLOSE  qty={qty}  id={iid}  symbol={sym}  product={pos.get('ProductType')}")
        if not args.dry_run:
            _place_close_order(client, index_config, pos, "FORCE_SQ")
            closed += 1
            time.sleep(0.3)

    cancelled = 0
    seen: set[int] = set()
    for order in order_book or []:
        tag = str(order.get("OrderUniqueIdentifier") or "")
        if not is_bot_sl_order_tag(tag):
            continue
        status = str(order.get("OrderStatus") or "")
        if not is_cancellable_order_status(status):
            continue
        oid = order.get("AppOrderID")
        if oid is None:
            continue
        try:
            oid_i = int(oid)
        except (TypeError, ValueError):
            continue
        if oid_i in seen:
            continue
        seen.add(oid_i)
        print(f"CANCEL SL  id={oid_i}  tag={tag}  status={status}")
        if not args.dry_run:
            try:
                client.cancel_order(oid_i, tag)
                cancelled += 1
            except Exception as e:
                print(f"  cancel failed: {e}")
            time.sleep(0.2)

    if not args.dry_run:
        extra = _cancel_bot_open_sl_orders(client, order_book=client.get_order_book())
        cancelled += extra
        reset_eod_state()
        for strategy in bot.STRATEGY_STATE.values():
            if strategy.get("status") in ("OPEN", "CLOSING"):
                bot.update_strategy(
                    strategy["name"],
                    status="CLOSED",
                    message="Manual force square-off",
                    sl_orders=[],
                    sl_tag_map={},
                )

    print(f"Done: close orders placed={closed}, SL cancels={cancelled}")
    if args.dry_run:
        print("(dry-run — no broker calls made)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
