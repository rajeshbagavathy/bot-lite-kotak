#!/usr/bin/env python3
"""
Micro live validation for Kotak Neo (read-only by default).

Requires: BROKER_BACKEND=kotak, Kotak credentials in env/SSM (see config.load_kotak_credentials).

Usage:
  BROKER_BACKEND=kotak ACC_NAME=... KOTAK_CONSUMER_KEY_S=... ... \\
    python scripts/kotak_live_smoke.py

Exercises: login, limits (margin), order_report, positions.
Does not place orders unless you pass --place-test-order (not implemented).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("BROKER_BACKEND", "kotak")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    args = parser.parse_args()

    from brokers.kotak_client import KotakNeoClient
    from config import load_kotak_credentials

    c = KotakNeoClient.from_config(load_kotak_credentials())
    c.login()

    limits = c._api.limits(segment="ALL", exchange="ALL", product="ALL")
    book = c._api.order_report()
    pos = c._api.positions()

    if args.json:
        print(json.dumps({"limits": limits, "order_report": book, "positions": pos}, default=str, indent=2))
        return 0

    net = limits.get("Net") if isinstance(limits, dict) else None
    print("limits.Net:", net)
    od = book.get("data") if isinstance(book, dict) else None
    print("order_report rows:", len(od) if isinstance(od, list) else "n/a")
    pd = pos.get("data") if isinstance(pos, dict) else None
    print("position rows:", len(pd) if isinstance(pd, list) else "n/a")
    print("smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
