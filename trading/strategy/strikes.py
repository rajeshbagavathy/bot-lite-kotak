"""Strike and hedge selection."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("xts-bot-lite")

_QUOTE_BATCH_SIZE = 25


def _ensure_chain_index(client: Any, index_config, expiry: str) -> int:
    warm = getattr(client, "warm_option_chain", None)
    if callable(warm):
        try:
            warm(index_config, expiry)
        except Exception:
            logger.debug("warm_option_chain failed", exc_info=True)
    reindex = getattr(client, "reindex_option_chain", None)
    if callable(reindex):
        try:
            return int(reindex(index_config, expiry))
        except Exception:
            logger.debug("reindex_option_chain failed", exc_info=True)
    idx = getattr(client, "_option_id_index", None)
    if isinstance(idx, dict):
        exp = (expiry or "").strip().upper()
        return sum(1 for k in idx if k[0] == index_config.name and k[1] == exp)
    return 0


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


def _otm_indexed_candidates(
    client: Any,
    index_config,
    expiry: str,
    option_type: str,
    atm_strike: int,
    max_steps: int,
) -> List[Tuple[int, int]]:
    """Far-OTM strikes from warmed ``_option_id_index`` only (no per-strike scrip search)."""
    _ensure_chain_index(client, index_config, expiry)
    direction = 1 if option_type.upper() == "CE" else -1
    strike_diff = int(index_config.strike_diff)
    ot = option_type.upper()
    exp = (expiry or "").strip().upper()
    name = index_config.name
    strike_to_iid: Dict[int, int] = {}
    idx = getattr(client, "_option_id_index", None)
    if isinstance(idx, dict):
        for (iname, e, o, strike), tid in idx.items():
            if iname == name and e == exp and o == ot:
                try:
                    strike_to_iid[int(strike)] = int(tid)
                except (TypeError, ValueError):
                    continue
    out: List[Tuple[int, int]] = []
    for i in range(1, max_steps + 1):
        strike = atm_strike + (i * strike_diff * direction)
        iid = strike_to_iid.get(int(strike))
        if iid is not None:
            out.append((int(strike), iid))
    return out


def _batch_quote_ltps(client: Any, index_config, instrument_ids: List[int]) -> Dict[int, float]:
    if not instrument_ids:
        return {}
    seg = int(index_config.option_ltp_segment)
    out: Dict[int, float] = {}
    for start in range(0, len(instrument_ids), _QUOTE_BATCH_SIZE):
        chunk = instrument_ids[start : start + _QUOTE_BATCH_SIZE]
        instruments = [{"exchangeSegment": seg, "exchangeInstrumentID": iid} for iid in chunk]
        try:
            ltp_map = client.get_ltp_map(instruments)
        except Exception:
            logger.warning("Hedge quote batch failed for %d instrument(s)", len(chunk), exc_info=True)
            continue
        if not isinstance(ltp_map, dict):
            continue
        for iid, ltp in ltp_map.items():
            if ltp is None:
                continue
            try:
                out[int(iid)] = float(ltp)
            except (TypeError, ValueError):
                continue
    return out


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
    """
    Pick far-OTM hedge from warmed chain index, then one batched Kotak quote for premiums.

    Matches pre-refactor behaviour (index + batch ``get_ltp_map``) without per-strike ``search_scrip``.
    """
    ot = option_type.upper()
    indexed_n = _ensure_chain_index(client, index_config, expiry)
    chain_ltps = _chain_ltp_lookup(client, index_config, expiry)
    candidates = _otm_indexed_candidates(client, index_config, expiry, ot, atm_strike, max_steps)
    if not candidates:
        logger.warning(
            "No indexed %s strikes within %d OTM steps of ATM %s (index_entries=%d)",
            ot,
            max_steps,
            atm_strike,
            indexed_n,
        )
        return None

    ltp_by_id: Dict[int, float] = {}
    need_quote: List[int] = []
    for strike, iid in candidates:
        ltp_val = chain_ltps.get((ot, strike))
        if ltp_val is None:
            ltp_val = _option_ltp_hint(client, iid)
        if ltp_val is not None:
            ltp_by_id[iid] = float(ltp_val)
        elif iid not in need_quote:
            need_quote.append(iid)

    if need_quote:
        ltp_by_id.update(_batch_quote_ltps(client, index_config, need_quote))

    best: Optional[Tuple[float, int, int, float]] = None
    for strike, iid in candidates:
        ltp_val = ltp_by_id.get(iid)
        if ltp_val is None:
            continue
        if not (min_premium <= ltp_val <= max_premium):
            continue
        diff = ltp_val - float(min_premium)
        if best is None or diff < best[0]:
            best = (diff, strike, iid, ltp_val)

    if best is None:
        quoted = [ltp_by_id[i] for _, i in candidates if i in ltp_by_id]
        sample = quoted[:5]
        logger.warning(
            "No %s hedge in ₹%.1f–₹%.1f band within %d steps of ATM %s "
            "(candidates=%d, index_entries=%d, quoted=%d, sample_ltps=%s)",
            ot,
            min_premium,
            max_premium,
            max_steps,
            atm_strike,
            len(candidates),
            indexed_n,
            len(quoted),
            sample,
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
