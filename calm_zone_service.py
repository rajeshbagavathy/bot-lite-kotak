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
    fetch_latest_spot_bar_row,
    get_ist_now,
    spot_bar_exists,
    upsert_spot_bar,
    upsert_spot_ohlc_only,
)
from xts_client import XTSClient

logger = logging.getLogger(__name__)

T = TypeVar("T")

ROLLING_FALLBACK_MINUTES = 25
RECOMPUTE_TAIL = 240
STARTUP_BACKFILL_LIMIT = 5000
MAX_RETRIES = 5
BASE_SLEEP_SEC = 60
MARKET_OPEN_HHMM = (9, 15)
MARKET_CLOSE_HHMM = (15, 30)

_HEALTH_LOCK = threading.Lock()
_HEALTH: Dict[str, Any] = {
    "started_at": None,
    "last_tick_started_at": None,
    "last_tick_completed_at": None,
    "last_tick_status": "idle",
    "last_error": None,
    "indices": {
        "NIFTY": {
            "last_fetch_count": 0,
            "last_latest_bar_time": None,
            "last_request_start_ist": None,
            "last_request_end_ist": None,
            "last_fetch_raw_count": 0,
            "last_fetch_fallback_count": 0,
            "last_fetch_used_fallback": False,
            "last_first_bar_unix": None,
            "last_last_bar_unix": None,
        },
        "SENSEX": {
            "last_fetch_count": 0,
            "last_latest_bar_time": None,
            "last_request_start_ist": None,
            "last_request_end_ist": None,
            "last_fetch_raw_count": 0,
            "last_fetch_fallback_count": 0,
            "last_fetch_used_fallback": False,
            "last_first_bar_unix": None,
            "last_last_bar_unix": None,
        },
    },
}


def _health_set(**kwargs: Any) -> None:
    with _HEALTH_LOCK:
        _HEALTH.update(kwargs)


def _health_set_index(index_name: str, fetch_count: Optional[int] = None, latest_bar_time: Optional[str] = None) -> None:
    idx = str(index_name).upper()
    with _HEALTH_LOCK:
        if idx not in _HEALTH["indices"]:
            _HEALTH["indices"][idx] = {"last_fetch_count": 0, "last_latest_bar_time": None}
        slot = _HEALTH["indices"][idx]
        if fetch_count is not None:
            slot["last_fetch_count"] = int(fetch_count)
        if latest_bar_time is not None:
            slot["last_latest_bar_time"] = latest_bar_time


def _health_set_index_fetch_meta(
    index_name: str,
    *,
    req_start: datetime,
    req_end: datetime,
    raw_count: int,
    fallback_count: int,
    used_fallback: bool,
    selected_bars: List[Dict[str, Any]],
) -> None:
    idx = str(index_name).upper()
    with _HEALTH_LOCK:
        if idx not in _HEALTH["indices"]:
            _HEALTH["indices"][idx] = {}
        slot = _HEALTH["indices"][idx]
        slot["last_request_start_ist"] = req_start.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")
        slot["last_request_end_ist"] = req_end.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")
        slot["last_fetch_raw_count"] = int(raw_count)
        slot["last_fetch_fallback_count"] = int(fallback_count)
        slot["last_fetch_used_fallback"] = bool(used_fallback)
        if selected_bars:
            slot["last_first_bar_unix"] = int(selected_bars[0].get("bar_unix") or 0)
            slot["last_last_bar_unix"] = int(selected_bars[-1].get("bar_unix") or 0)
        else:
            slot["last_first_bar_unix"] = None
            slot["last_last_bar_unix"] = None


def get_calm_zone_health_snapshot() -> Dict[str, Any]:
    with _HEALTH_LOCK:
        return {
            "started_at": _HEALTH.get("started_at"),
            "last_tick_started_at": _HEALTH.get("last_tick_started_at"),
            "last_tick_completed_at": _HEALTH.get("last_tick_completed_at"),
            "last_tick_status": _HEALTH.get("last_tick_status"),
            "last_error": _HEALTH.get("last_error"),
            "indices": {k: dict(v) for k, v in (_HEALTH.get("indices") or {}).items()},
        }


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


def canonical_spot_bar_time_ist(ts: int) -> str:
    """
    Single DB key per 1m bar: IST wall time with seconds forced to :00.
    Prevents duplicate rows for the same minute (e.g. ...:18 vs ...:59 from vendor unix).
    """
    ts = int(ts) + CALM_ZONE_BAR_UNIX_OFFSET_SEC
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
    dt = dt.replace(second=0, microsecond=0)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _coarse_minute_key_from_bar_time(bar_time: str) -> str:
    """Normalize legacy bar_time strings to the same canonical minute key."""
    raw = str(bar_time).strip().replace("T", " ")
    if len(raw) >= 16:
        return raw[:16] + ":00"
    return raw


def _dedupe_spot_rows_for_recompute(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Collapse multiple DB rows that belong to the same calendar minute so the 5-bar
    sliding window uses exactly one OHLC per minute.
    """
    best: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        bu = r.get("bar_unix")
        if bu is not None:
            try:
                key = canonical_spot_bar_time_ist(_normalize_unix_ts(int(bu)))
            except (TypeError, ValueError):
                key = _coarse_minute_key_from_bar_time(str(r.get("bar_time") or ""))
        else:
            key = _coarse_minute_key_from_bar_time(str(r.get("bar_time") or ""))
        prev = best.get(key)
        if prev is None:
            nr = dict(r)
            nr["bar_time"] = key
            best[key] = nr
            continue
        take_new = False
        pl = int(prev.get("calm_locked") or 0)
        rl = int(r.get("calm_locked") or 0)
        if rl > pl:
            take_new = True
        elif prev.get("range_5m") is None and r.get("range_5m") is not None:
            take_new = True
        elif (prev.get("range_5m") is None) == (r.get("range_5m") is None):
            if int(r.get("bar_unix") or 0) > int(prev.get("bar_unix") or 0):
                take_new = True
        if take_new:
            nr = dict(r)
            nr["bar_time"] = key
            best[key] = nr
    return sorted(
        best.values(),
        key=lambda x: int(x["bar_unix"]) if x.get("bar_unix") is not None else 0,
    )


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

    def _call(s: datetime, e: datetime):
        return client.get_spot_ohlc_bars(cfg, s, e)

    raw = _with_retries(lambda: _call(start, end_k), f"OHLC {index_name}") or []
    selected = raw
    used_fallback = False
    fallback_count = 0
    # Safety: if configured lookback is too narrow (or upstream returns too little),
    # retry with full-session window so calm recompute always has enough context.
    if len(raw) < 5:
        session_start = _cash_session_start_ist(end_k)
        wider = _with_retries(
            lambda: _call(session_start, end_k),
            f"OHLC {index_name} full-session-fallback",
        ) or []
        fallback_count = len(wider)
        if len(wider) >= len(raw):
            selected = wider
            used_fallback = True
    _health_set_index_fetch_meta(
        index_name,
        req_start=start,
        req_end=end_k,
        raw_count=len(raw),
        fallback_count=fallback_count,
        used_fallback=used_fallback,
        selected_bars=selected,
    )
    return selected


def _upsert_ohlc_rows(index_name: str, bars: List[Dict[str, Any]]) -> None:
    for b in bars:
        ts = _normalize_unix_ts(b["bar_unix"])
        bar_time = canonical_spot_bar_time_ist(ts)
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


def recompute_calm_metrics_for_index(index_name: str, limit: int = RECOMPUTE_TAIL) -> None:
    """
    One-time calm metrics per 1m bar (static after first successful write).

    Rows are deduped by canonical minute key so the 5-bar window never double-counts a minute.
    ``calm_locked`` / existing ``range_5m`` skip further math for that minute.
    """
    raw = fetch_spot_bars_asc_for_recompute(index_name, limit)
    rows = _dedupe_spot_rows_for_recompute(raw)
    for i, r in enumerate(rows):
        if i < 4:
            continue
        if int(r.get("calm_locked") or 0) == 1:
            continue
        if r.get("range_5m") is not None:
            continue
        bu = r.get("bar_unix")
        if bu is not None:
            try:
                bu = int(bu)
            except (TypeError, ValueError):
                bu = None
        bt = canonical_spot_bar_time_ist(bu) if bu is not None else _coarse_minute_key_from_bar_time(
            str(r.get("bar_time") or "")
        )
        o = float(r["open"])
        h = float(r["high"])
        lo = float(r["low"])
        c = float(r["close"])
        vol = float(r["volume"]) if r.get("volume") is not None else None
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
        _health_set_index(index_name, fetch_count=len(bars))
        if not bars:
            continue
        _upsert_ohlc_rows(index_name, bars)
        # Incremental pass: compute only recent unlocked minutes; locked rows stay immutable.
        recompute_calm_metrics_for_index(index_name, RECOMPUTE_TAIL)
        latest = fetch_latest_spot_bar_row(index_name) or {}
        _health_set_index(index_name, latest_bar_time=latest.get("bar_time"))


def backfill_today_calm_once() -> None:
    """
    Startup-only backfill: if today's 1m rows already exist, compute calm metrics once
    for all currently-available minutes and lock them.
    """
    for index_name in ("NIFTY", "SENSEX"):
        recompute_calm_metrics_for_index(index_name, STARTUP_BACKFILL_LIMIT)


def _run_loop(client: XTSClient, stop: threading.Event) -> None:
    logger.info("Calm zone monitor thread started (DB=%s)", DB_PATH)
    _health_set(started_at=get_ist_now().strftime("%Y-%m-%d %H:%M:%S"), last_tick_status="starting")
    try:
        backfill_today_calm_once()
    except Exception:
        logger.exception("Startup calm-zone backfill failed")
        _health_set(last_error="startup_backfill_failed", last_tick_status="startup_failed")
    while not stop.is_set():
        try:
            _health_set(last_tick_started_at=get_ist_now().strftime("%Y-%m-%d %H:%M:%S"), last_tick_status="running")
            calm_zone_tick(client)
            _health_set(
                last_tick_completed_at=get_ist_now().strftime("%Y-%m-%d %H:%M:%S"),
                last_tick_status="ok",
                last_error=None,
            )
        except Exception:
            logger.exception("Calm zone tick failed")
            _health_set(
                last_tick_completed_at=get_ist_now().strftime("%Y-%m-%d %H:%M:%S"),
                last_tick_status="error",
                last_error="tick_failed",
            )
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
