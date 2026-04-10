#!/usr/bin/env python3
"""
Poll volatility-monitor API and detect row mutations for same bar_time.

Example:
python diagnose_volatility_endpoint.py \
  --url "http://optionedge.site/api/volatility-monitor?page=1&page_size=120" \
  --username rbaga \
  --password diyaadhiv \
  --interval 2 \
  --duration 300
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import time
from typing import Any, Dict, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


WATCH_FIELDS = (
    "range_5m",
    "net_body",
    "body_range_ratio",
    "is_calmzone",
    "calm_locked",
)


def _auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def fetch_json(url: str, username: str, password: str, timeout_sec: float) -> Dict[str, Any]:
    req = Request(url)
    req.add_header("Authorization", _auth_header(username, password))
    with urlopen(req, timeout=timeout_sec) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def row_signature(row: Dict[str, Any]) -> Tuple[Any, ...]:
    return tuple(row.get(k) for k in WATCH_FIELDS)


def locked_rows_checksum(rows: List[Dict[str, Any]]) -> str:
    locked = [
        {
            "bar_time": r.get("bar_time"),
            "range_5m": r.get("range_5m"),
            "net_body": r.get("net_body"),
            "body_range_ratio": r.get("body_range_ratio"),
            "is_calmzone": r.get("is_calmzone"),
            "calm_locked": r.get("calm_locked"),
        }
        for r in rows
        if int(r.get("calm_locked") or 0) == 1
    ]
    locked.sort(key=lambda x: str(x.get("bar_time")))
    payload = json.dumps(locked, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def main() -> int:
    p = argparse.ArgumentParser(description="Poll volatility endpoint and detect calm row mutations.")
    p.add_argument("--url", required=True, help="Full API URL including page/page_size")
    p.add_argument("--username", required=True, help="Basic auth username")
    p.add_argument("--password", required=True, help="Basic auth password")
    p.add_argument("--interval", type=float, default=2.0, help="Poll interval seconds")
    p.add_argument("--duration", type=float, default=300.0, help="Total run time seconds")
    p.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout seconds")
    args = p.parse_args()

    seen: Dict[str, Tuple[Any, ...]] = {}
    start = time.time()
    poll_no = 0
    last_checksum = ""
    mutation_count = 0

    print("Starting volatility diagnostics...")
    print(f"URL: {args.url}")
    print(f"Interval: {args.interval}s | Duration: {args.duration}s")
    print("-" * 80)

    while True:
        now = time.time()
        if now - start > args.duration:
            break
        poll_no += 1
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        try:
            payload = fetch_json(args.url, args.username, args.password, args.timeout)
            rows = payload.get("rows") or []
            if not isinstance(rows, list):
                print(f"[{ts}] poll={poll_no} ERROR: rows is not a list")
                time.sleep(args.interval)
                continue

            checksum = locked_rows_checksum(rows)
            latest = rows[0].get("bar_time") if rows else "-"
            print(
                f"[{ts}] poll={poll_no} rows={len(rows)} latest={latest} locked_checksum={checksum}"
                + (" *changed*" if last_checksum and checksum != last_checksum else "")
            )
            last_checksum = checksum

            for r in rows:
                bt = str(r.get("bar_time") or "")
                if not bt:
                    continue
                sig = row_signature(r)
                prev = seen.get(bt)
                if prev is not None and prev != sig:
                    mutation_count += 1
                    print(f"  MUTATION #{mutation_count} bar_time={bt}")
                    print(f"    prev={prev}")
                    print(f"    now ={sig}")
                else:
                    seen[bt] = sig
        except HTTPError as e:
            print(f"[{ts}] poll={poll_no} HTTPError {e.code}: {e.reason}")
        except URLError as e:
            print(f"[{ts}] poll={poll_no} URLError: {e}")
        except Exception as e:
            print(f"[{ts}] poll={poll_no} ERROR: {e}")

        time.sleep(max(0.2, args.interval))

    print("-" * 80)
    print(f"Done. polls={poll_no} distinct_bar_times={len(seen)} mutations={mutation_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

