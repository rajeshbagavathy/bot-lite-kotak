"""Strike and hedge selection."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("xts-bot-lite")


def _chain_ltp_lookup(client: Any, index_config, expiry: str) -> Dict[Tuple[str, int], float]:
    fn = getattr(client, "chain_ltp_map", None)
    if callable(fn):
        try:
            raw = fn(index_config, expiry)
            if isinstance(raw, dict):
                return raw
        except Exception:
            logger.debug("chain_ltp_map failed", exc_info=True)
    return {}


def _option_ltp_hint(client: Any, instrument_id: int) -> Optional[float]:
    meta = getattr(client, "_token_meta", None)
    if not isinstance(meta, dict):
        return None
    tm = meta.get(int(instrument_id)) or {}
    if not isinstance(tm, dict):
        return None
    hint = tm.get("ltp_hint")
    if hint is None:
        return None
    try:
        return float(hint)
    except (TypeError, ValueError):
        return None


def find_hedge_by_target_premium(
    client: Any,
    index_config,
    expiry: str,
    option_type: str,
    atm_strike: int,
    target_premium: float,
    min_premium: float,
    max_premium: float,
    max_steps: int = 40,
) -> Optional[dict]:
    """Pick far-OTM hedge using in-memory scrip LTP only (no per-strike live quotes)."""
    direction = 1 if option_type.upper() == "CE" else -1
    strike_diff = int(index_config.strike_diff)
    ot = option_type.upper()
    chain_ltps = _chain_ltp_lookup(client, index_config, expiry)
    best: Optional[Tuple[float, int, int, float]] = None
    for i in range(1, max_steps + 1):
        strike = atm_strike + (i * strike_diff * direction)
        instrument_id = client.get_option_instrument_id(index_config, expiry, ot, strike)
        if not instrument_id:
            continue
        try:
            iid = int(instrument_id)
        except (TypeError, ValueError):
            continue
        ltp_val = chain_ltps.get((ot, int(strike)))
        if ltp_val is None:
            ltp_val = _option_ltp_hint(client, iid)
        if ltp_val is None:
            continue
        if not (min_premium <= ltp_val <= max_premium):
            continue
        diff = ltp_val - float(min_premium)
        if best is None or diff < best[0]:
            best = (diff, strike, iid, ltp_val)
    if best is None:
        logger.warning(
            "No %s hedge in ₹%.1f–₹%.1f band within %d steps of ATM %s (chain_ltps=%d)",
            ot,
            min_premium,
            max_premium,
            max_steps,
            atm_strike,
            len(chain_ltps),
        )
        return None
    _, strike, instrument_id, ltp_val = best
    return {"strike": strike, "instrument_id": instrument_id, "ltp": ltp_val}


def find_strike_by_premium(
    client: Any,
    index_config,
    expiry: str,
    option_type: str,
    atm_strike: int,
    target_premium: float,
    min_premium: float,
    max_premium: float,
    max_steps: int = 30,
) -> Optional[Tuple[int, int]]:
    strike_diff = int(index_config.strike_diff)
    ot = option_type.upper()
    chain_ltps = _chain_ltp_lookup(client, index_config, expiry)
    strikes_to_check: List[int] = [atm_strike]
    for i in range(1, max_steps + 1):
        strikes_to_check.append(atm_strike + i * strike_diff)
        strikes_to_check.append(atm_strike - i * strike_diff)
    candidates: List[Tuple[int, int]] = []
    for strike in strikes_to_check:
        instrument_id = client.get_option_instrument_id(index_config, expiry, ot, strike)
        if instrument_id:
            candidates.append((strike, int(instrument_id)))
    if not candidates:
        return None
    need_quote: List[Tuple[int, int]] = []
    best: Optional[Tuple[float, int, int]] = None
    for strike, instrument_id in candidates:
        ltp = chain_ltps.get((ot, int(strike)))
        if ltp is None:
            ltp = _option_ltp_hint(client, instrument_id)
        if ltp is None:
            need_quote.append((strike, instrument_id))
            continue
        try:
            ltp_val = float(ltp)
        except (TypeError, ValueError):
            continue
        if not (min_premium <= ltp_val <= max_premium):
            continue
        diff = abs(ltp_val - target_premium)
        if best is None or diff < best[0]:
            best = (diff, strike, instrument_id)
    if need_quote and best is None:
        instruments = [
            {"exchangeSegment": index_config.option_ltp_segment, "exchangeInstrumentID": iid}
            for _, iid in need_quote
        ]
        ltp_map = client.get_ltp_map(instruments)
        for strike, instrument_id in need_quote:
            ltp = ltp_map.get(instrument_id)
            if ltp is None:
                continue
            try:
                ltp_val = float(ltp)
            except (TypeError, ValueError):
                continue
            if not (min_premium <= ltp_val <= max_premium):
                continue
            diff = abs(ltp_val - target_premium)
            if best is None or diff < best[0]:
                best = (diff, strike, instrument_id)
    if best is None:
        return None
    _, strike, instrument_id = best
    return (strike, instrument_id)
