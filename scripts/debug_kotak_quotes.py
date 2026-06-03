#!/usr/bin/env python3
"""Print Kotak Neo quote JSON for index tokens (session + URL variants). Run from repo root.

Auth: uses the same .env as the bot. If the bot is already running with KOTAK_TOTP (one-shot),
that value in .env is usually stale—prefer KOTAK_TOTP_SECRET, or pass a fresh code::

    python3 scripts/debug_kotak_quotes.py --totp 123456
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import load_kotak_credentials  # noqa: E402
from brokers.kotak_client import KOTAK_INDEX_META, KotakNeoClient  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Dump Kotak quote JSON for index variants (separate login).")
    p.add_argument(
        "--totp",
        metavar="CODE",
        help="Current 6-digit TOTP (overrides stale KOTAK_TOTP from .env for this run only)",
    )
    args = p.parse_args()
    if args.totp:
        code = str(args.totp).strip().replace(" ", "")
        if not code.isdigit() or len(code) != 6:
            sys.exit("error: --totp must be exactly 6 digits")
        os.environ["KOTAK_TOTP"] = code

    creds = load_kotak_credentials()
    client = KotakNeoClient.from_config(creds)
    client.login()
    print("base_url:", getattr(client._api.configuration, "base_url", None))
    for name in sorted(KOTAK_INDEX_META):
        for token, seg in client._index_quote_token_variants(name):
            inst = [{"instrument_token": token, "exchange_segment": seg}]
            for qt in ("ltp", "all", "ohlc"):
                for is_ix in (True, False):
                    raw = client._quotes_get(inst, qt, is_index=is_ix)
                    print(f"\n=== {name} token={token!r} seg={seg} qt={qt} isIndex={is_ix} ===")
                    print(json.dumps(raw, indent=2, default=str)[:8000])
                    print("parsed LTP:", client._parse_index_ltp_from_quotes(raw))


if __name__ == "__main__":
    main()
