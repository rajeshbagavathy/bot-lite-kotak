"""Strike and hedge selection."""
from __future__ import annotations

from typing import Any, List, Optional, Tuple


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
    direction = 1 if option_type.upper() == "CE" else -1
    strike_diff = int(index_config.strike_diff)
    candidates: List[Tuple[int, int]] = []
    for i in range(1, max_steps + 1):
        strike = atm_strike + (i * strike_diff * direction)
        instrument_id = client.get_option_instrument_id(index_config, expiry, option_type.upper(), strike)
        if instrument_id:
            try:
                candidates.append((strike, int(instrument_id)))
            except (TypeError, ValueError):
                continue
    if not candidates:
        return None
    instruments = [
        {"exchangeSegment": index_config.option_ltp_segment, "exchangeInstrumentID": iid}
        for _, iid in candidates
    ]
    ltp_map = client.get_ltp_map(instruments)
    best = None
    for strike, instrument_id in candidates:
        ltp = ltp_map.get(instrument_id)
        if ltp is None:
            continue
        try:
            ltp_val = float(ltp)
        except (TypeError, ValueError):
            continue
        if not (min_premium <= ltp_val <= max_premium):
            continue
        diff = ltp_val - float(min_premium)
        if best is None or diff < best[0]:
            best = (diff, strike, instrument_id, ltp_val)
    if best is None:
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
    strikes_to_check: List[int] = [atm_strike]
    for i in range(1, max_steps + 1):
        strikes_to_check.append(atm_strike + i * strike_diff)
        strikes_to_check.append(atm_strike - i * strike_diff)
    candidates: List[Tuple[int, int]] = []
    for strike in strikes_to_check:
        instrument_id = client.get_option_instrument_id(index_config, expiry, option_type.upper(), strike)
        if instrument_id:
            candidates.append((strike, int(instrument_id)))
    if not candidates:
        return None
    instruments = [
        {"exchangeSegment": index_config.option_ltp_segment, "exchangeInstrumentID": iid}
        for _, iid in candidates
    ]
    ltp_map = client.get_ltp_map(instruments)
    best: Optional[Tuple[float, int, int]] = None
    for strike, instrument_id in candidates:
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
