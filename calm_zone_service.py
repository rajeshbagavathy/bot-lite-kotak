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

from config import (
    CALM_ZONE_BAR_UNIX_OFFSET_SEC,
    CALM_ZONE_OHLC_FREEZE_AFTER_SEC,
    CALM_ZONE_OHLC_LOOKBACK_MINUTES,
    INDEX_CONFIGS,
)
from db import (
    DB_PATH,
    fetch_spot_bars_asc_for_recompute,
    get_ist_now,
    spot_bar_exists,
    upsert_spot_bar,
    upsert_spot_ohlc_only,
)
from xts_client import XTSClient

logger = logging.getLogger(__name__)

T = TypeVar("T")

ROLLING_FALLBACK_MINUTES = 25
RECOMPUTE_TAIL = 500
MAX_RETRIES = 5
BASE_SLEEP_SEC = 60
MARKET_OPEN_HHMM = (9, 15)
MARKET_CLOSE_HHMM = (15, 30)


def _is_market_hours_ist(now: Optional[datetime] = None) -> bool:
    t = (now or get_ist_now()).time()
    open_h, open_m = MARKET_OPEN_HHMM
    close_h, close_m = MARKET_CLOSE_HHMM
    start_ok = (t.hour, t.minute) >= (open_h, open_m)
    end_ok = (t.hour, t.minute) <= (close_h, close_m)
    return start_ok and end_ok


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


def _cash_session_start_ist(end_ist: datetime) -> datetime:
    """Today's 09:15 Asia/Kolkata (same calendar day as ``end_ist``)."""
    kolkata = ZoneInfo("Asia/Kolkata")
    e = end_ist.astimezone(kolkata)
    open_h, open_m = MARKET_OPEN_HHMM
    return e.replace(hour=open_h, minute=open_m, second=0, microsecond=0)


def _fetch_ohlc_window(client: XTSClient, index_name: str) -> List[Dict[str, Any]]:
    cfg = INDEX_CONFIGS[index_name]
    end = get_ist_now()
    kolkata = ZoneInfo("Asia/Kolkata")
    end_k = end.astimezone(kolkata)
    lb = CALM_ZONE_OHLC_LOOKBACK_MINUTES
    if lb is not None and lb > 0:
        start = end_k - timedelta(minutes=lb)
    else:
        start = _cash_session_start_ist(end_k)
        if end_k < start:
            start = end_k - timedelta(minutes=ROLLING_FALLBACK_MINUTES)

    def _call():
        return client.get_spot_ohlc_bars(cfg, start, end_k)

    raw = _with_retries(_call, f"OHLC {index_name}")
    return raw or []


def _upsert_ohlc_rows(index_name: str, bars: List[Dict[str, Any]]) -> None:
    for b in bars:
        ts = _normalize_unix_ts(b["bar_unix"])
        bar_time = bar_unix_to_ist_str(ts)
        if CALM_ZONE_OHLC_FREEZE_AFTER_SEC > 0:
            age = time.time() - float(ts)
            if age > float(CALM_ZONE_OHLC_FREEZE_AFTER_SEC) and spot_bar_exists(index_name, bar_time):
                continue
        vol = b.get("volume")
        upsert_spot_ohlc_only(
            index_name,
            bar_time,
            float(b["open"]),
            float(b["high"]),
            float(b["low"]),
            float(b["close"]),
            float(vol) if vol is not None else None,
            bar_unix=ts,
        )


def recompute_calm_metrics_for_index(index_name: str) -> None:
    """
    One-time calm metrics per 1m bar (static after first successful write).

    For each minute we need five consecutive bars ending at that minute. Once ``range_5m``
    has been stored for that row, we never recompute or overwrite Range / Ratio / Calm —
    OHLC for that minute may still refresh from the API via ``_upsert_ohlc_rows`` until
    OHLC freeze applies, but calm-zone flags stay fixed for that bar_time.
    """
    rows = fetch_spot_bars_asc_for_recompute(index_name, RECOMPUTE_TAIL)
    for i, r in enumerate(rows):
        if i < 4:
            continue
        if r.get("range_5m") is not None:
            continue
        bt = r["bar_time"]
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
            bt,
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
    was_open = None
    while not stop.is_set():
        try:
            now = get_ist_now()
            in_hours = _is_market_hours_ist(now)
            if in_hours:
                calm_zone_tick(client)
            elif was_open is not False:
                logger.info(
                    "Calm zone ingestion paused outside market hours (%02d:%02d-%02d:%02d IST).",
                    MARKET_OPEN_HHMM[0],
                    MARKET_OPEN_HHMM[1],
                    MARKET_CLOSE_HHMM[0],
                    MARKET_CLOSE_HHMM[1],
                )
            was_open = in_hours
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
