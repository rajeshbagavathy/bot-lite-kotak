#!/usr/bin/env python3
"""Inspect warmed Kotak option index vs hedge OTM strike grid (run on EC2 after login)."""
from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import INDEX_CONFIGS, load_kotak_credentials  # noqa: E402
from brokers.kotak_client import KotakNeoClient  # noqa: E402


def main() -> None:
    creds = load_kotak_credentials()
    client = KotakNeoClient.from_config(creds)
    client.login()
    cfg = INDEX_CONFIGS["NIFTY"]
    exp = "09JUN2026"
    atm = 23150
    diff = int(cfg.strike_diff)
    n = client.warm_option_chain(cfg, exp)
    print("chain_rows:", n, "indexed:", client._indexed_strike_count("NIFTY", exp))
    pe_keys = sorted(k[3] for k in client._option_id_index if k[:3] == ("NIFTY", exp, "PE"))
    ce_keys = sorted(k[3] for k in client._option_id_index if k[:3] == ("NIFTY", exp, "CE"))
    print("PE strikes sample:", pe_keys[:8], "...", pe_keys[-8:] if pe_keys else [])
    print("CE strikes sample:", ce_keys[:8], "...", ce_keys[-8:] if ce_keys else [])
    for ot, direction in (("PE", -1), ("CE", 1)):
        print(f"\n{ot} OTM grid from ATM {atm}:")
        for i in range(1, 6):
            strike = atm + i * diff * direction
            key = ("NIFTY", exp, ot, strike)
            print(f"  step{i} {strike}: index={client._option_id_index.get(key)}")


if __name__ == "__main__":
    main()
