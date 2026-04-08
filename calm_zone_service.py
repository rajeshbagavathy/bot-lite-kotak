"""
Background ingestion of 1m spot OHLC and calm-zone metrics (NIFTY / SENSEX).
Runs in a daemon thread; does not use the main ``schedule`` loop.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, TypeVar
from zoneinfo import ZoneInfo

from calm_zone_math import compute_calm_metrics
from config import CALM_ZONE_BAR_UNIX_OFFSET_SEC, INDEX_CONFIGS
from db import (
    DB_PATH,
    fetch_spot_bars_asc_for_recompute,
    get_ist_now,
    upsert_spot_bar,
)
from xts_client import XTSClient

logger = logging.getLogger(__name__)

T = TypeVar("T")

FETCH_LOOKBACK_MINUTES = 25
RECOMPUTE_TAIL = 500
MAX_RETRIES = 5
BASE_SLEEP_SEC = 60


def _normalize_unix_ts(ts: float) -> int:
    t = int(ts)
    if t > 1_000_000_000_000:
        return t // 1000
    return t


def bar_unix_to_ist_str(ts: int) -> str:
    """POSIX seconds (UTC-based) → Asia/Kolkata wall clock for DB string + optional vendor offset."""
    ts = int(ts) + CALM_ZONE_BAR_UNIX_OFFSET_SEC
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _with_retries(fn: Callable[[], T], label: str) -> Optional[T]:
    delay = 1.0
    last_err: Optional[BaseException] = None
    for attempt in range(MAX_RETRIES):
        try:
            return fn()
        except Exception as e:
            last_err = e
            logger.warning("%s attempt %s/%s failed: %s", label, attempt + 1, MAX_RETRIES, e)
            if attempt < MAX_RETRIES - 1:
                jitter = random.uniform(0, 0.5)
                time.sleep(min(delay + jitter, 30.0))
                delay = min(delay * 2, 30.0)
    logger.error("%s failed after %s tries: %s", label, MAX_RETRIES, last_err)
    return None


def _fetch_ohlc_window(client: XTSClient, index_name: str) -> List[Dict[str, Any]]:
    cfg = INDEX_CONFIGS[index_name]
    end = get_ist_now()
    start = end - timedelta(minutes=FETCH_LOOKBACK_MINUTES)

    def _call():
        return client.get_spot_ohlc_bars(cfg, start, end)

    raw = _with_retries(_call, f"OHLC {index_name}")
    return raw or []


def _upsert_ohlc_rows(index_name: str, bars: List[Dict[str, Any]]) -> None:
    for b in bars:
        ts = _normalize_unix_ts(b["bar_unix"])
        bar_time = bar_unix_to_ist_str(ts)
        vol = b.get("volume")
        upsert_spot_bar(
            index_name,
            bar_time,
            float(b["open"]),
            float(b["high"]),
            float(b["low"]),
            float(b["close"]),
            float(vol) if vol is not None else None,
            None,
            None,
            None,
            False,
            bar_unix=ts,
        )


def recompute_calm_metrics_for_index(index_name: str) -> None:
    rows = fetch_spot_bars_asc_for_recompute(index_name, RECOMPUTE_TAIL)
    for i, r in enumerate(rows):
        o = float(r["open"])
        h = float(r["high"])
        lo = float(r["low"])
        c = float(r["close"])
        vol = float(r["volume"]) if r.get("volume") is not None else None
        bu = r.get("bar_unix")
        if bu is not None:
            try:
                bu = int(bu)
            except (TypeError, ValueError):
                bu = None
        if i < 4:
            upsert_spot_bar(
                index_name,
                r["bar_time"],
                o,
                h,
                lo,
                c,
                vol,
                None,
                None,
                None,
                False,
                bar_unix=bu,
            )
            continue
        window = rows[i - 4 : i + 1]
        core = [
            {"open": x["open"], "high": x["high"], "low": x["low"], "close": x["close"]}
            for x in window
        ]
        m = compute_calm_metrics(core, index_name)
        if not m:
            continue
        upsert_spot_bar(
            index_name,
            r["bar_time"],
            o,
            h,
            lo,
            c,
            vol,
            m["range_5m"],
            m["net_body"],
            m["body_range_ratio"],
            bool(m["is_calmzone"]),
            bar_unix=bu,
        )


def calm_zone_tick(client: XTSClient) -> None:
    for index_name in ("NIFTY", "SENSEX"):
        bars = _fetch_ohlc_window(client, index_name)
        if not bars:
            continue
        _upsert_ohlc_rows(index_name, bars)
        recompute_calm_metrics_for_index(index_name)


def _run_loop(client: XTSClient, stop: threading.Event) -> None:
    logger.info("Calm zone monitor thread started (DB=%s)", DB_PATH)
    while not stop.is_set():
        try:
            calm_zone_tick(client)
        except Exception:
            logger.exception("Calm zone tick failed")
        stop.wait(BASE_SLEEP_SEC)


def start_calm_zone_monitor_thread(client: XTSClient) -> threading.Event:
    """
    Spawn daemon thread that runs ``calm_zone_tick`` every ~60s.
    Returns a threading.Event; set it to request shutdown (optional).
    """
    stop = threading.Event()

    def _target() -> None:
        _run_loop(client, stop)

    t = threading.Thread(target=_target, name="CalmZoneMonitor", daemon=True)
    t.start()
    return stop
