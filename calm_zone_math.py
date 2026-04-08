"""Pure calm-zone metrics over five 1-minute bars (oldest first)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def range_max_threshold(index_name: str) -> float:
    return 50.0 if index_name == "NIFTY" else 120.0


def compute_calm_metrics(five_bars: List[Dict[str, Any]], index_name: str) -> Optional[Dict[str, Any]]:
    """
    ``five_bars``: five consecutive 1m bars, oldest first. Each needs keys:
    open, high, low, close (float-like).
    """
    if len(five_bars) != 5:
        return None
    highs = [float(b["high"]) for b in five_bars]
    lows = [float(b["low"]) for b in five_bars]
    range_5m = max(highs) - min(lows)
    open_5m_ago = float(five_bars[0]["open"])
    close_t = float(five_bars[-1]["close"])
    net_body = abs(close_t - open_5m_ago)
    if range_5m <= 0:
        return {
            "range_5m": range_5m,
            "net_body": net_body,
            "body_range_ratio": None,
            "is_calmzone": False,
        }
    ratio = net_body / range_5m
    thr = range_max_threshold(index_name)
    is_calm = bool(range_5m < thr and ratio < 0.25)
    return {
        "range_5m": range_5m,
        "net_body": net_body,
        "body_range_ratio": ratio,
        "is_calmzone": is_calm,
    }
