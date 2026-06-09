#!/usr/bin/env python3
"""Dump Kotak positions + MTM breakdown (requires active Kotak session / TOTP)."""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("BROKER_BACKEND", "kotak")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--totp", help="6-digit TOTP if session not ready")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    from brokers.factory import create_broker_client
    from config import INDEX_CONFIGS, pick_index_and_expiry
    from mtm import (
        calculate_mtm,
        calculate_mtm_from_kotak_broker_pnl,
        calculate_mtm_kotak_amounts,
        mtm_position_breakdown,
    )

    client = create_broker_client()
    if args.totp:
        client.login_with_totp(args.totp)
    else:
        client.login()

    index_config, expiry = pick_index_and_expiry(client)
    warm = getattr(client, "warm_option_chain", None)
    if callable(warm):
        warm(index_config, expiry)

    raw = client._api.positions()
    positions = client.get_positions()
    instruments = [
        {"exchangeSegment": index_config.option_ltp_segment, "exchangeInstrumentID": p["ExchangeInstrumentId"]}
        for p in positions
        if int(p.get("ExchangeInstrumentId") or 0) != 0
    ]
    ltp_map = client.get_ltp_map(instruments)

    amt = calculate_mtm_kotak_amounts(positions, ltp_map)
    rpnl = calculate_mtm_from_kotak_broker_pnl(positions)
    xts = calculate_mtm(positions, ltp_map)
    breakdown = mtm_position_breakdown(positions, ltp_map)

    payload = {
        "expiry": expiry,
        "raw_row_count": len((raw or {}).get("data") or []),
        "normalized_positions": len(positions),
        "mtm_kotak_amounts": amt,
        "mtm_rpnl_upnl": rpnl,
        "mtm_xts_style": {"realized": xts[0], "unrealized": xts[1], "total": xts[2]},
        "breakdown": breakdown,
        "raw_sample": ((raw or {}).get("data") or [])[:3],
    }
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print("expiry:", expiry)
        print("positions:", len(positions), "raw rows:", payload["raw_row_count"])
        print("kotak_amounts MTM:", amt)
        print("rPNL/uPNL MTM:", rpnl)
        print("xts_style MTM:", xts)
        for row in breakdown:
            print(
                f"  {row['symbol']} qty={row['qty']} ltp={row['ltp']} "
                f"booked={row['booked']:.2f} total={row['total_pnl']:.2f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
