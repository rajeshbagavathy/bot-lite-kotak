import math
from typing import Dict, Iterable, List, Tuple


def _safe_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def calculate_mtm(positions: Iterable[dict], ltp_map: Dict[int, float]) -> Tuple[float, float, float]:
    overall_mtm = 0.0
    overall_realized = 0.0
    overall_unrealized = 0.0

    for pos in positions:
        instrument_id = _safe_int(pos.get("ExchangeInstrumentId"))
        if instrument_id == 0:
            continue
        ltp = ltp_map.get(instrument_id)
        if ltp is None:
            continue

        open_sell_qty = _safe_int(pos.get("OpenSellQuantity"))
        open_buy_qty = _safe_int(pos.get("OpenBuyQuantity"))
        quantity = _safe_int(pos.get("Quantity"))
        multiplier = _safe_int(pos.get("Multiplier"), 1)

        if open_sell_qty == 0:
            realized = 0.0
        else:
            total_squared_off = min(open_buy_qty, open_sell_qty)
            sell_weighted = _safe_float(pos.get("SumOfTradedQuantityAndPriceSell")) / max(open_sell_qty, 1)
            buy_weighted = _safe_float(pos.get("SumOfTradedQuantityAndPriceBuy")) / max(open_buy_qty, 1)
            if math.isnan(sell_weighted):
                sell_weighted = 0.0
            if math.isnan(buy_weighted):
                buy_weighted = 0.0
            realized = total_squared_off * (sell_weighted - buy_weighted) * multiplier

        if quantity > 0:
            buy_weighted = _safe_float(pos.get("SumOfTradedQuantityAndPriceBuy")) / max(open_buy_qty, 1)
            if math.isnan(buy_weighted):
                buy_weighted = 0.0
            unrealized = abs(quantity) * (ltp - buy_weighted) * multiplier
        else:
            sell_weighted = _safe_float(pos.get("SumOfTradedQuantityAndPriceSell")) / max(open_sell_qty, 1)
            if math.isnan(sell_weighted):
                sell_weighted = 0.0
            unrealized = abs(quantity) * (sell_weighted - ltp) * multiplier

        overall_realized += realized
        overall_unrealized += unrealized
        overall_mtm += realized + unrealized

    return overall_realized, overall_unrealized, overall_mtm


def calculate_mtm_for_instruments(
    positions: List[dict],
    ltp_map: Dict[int, float],
    instrument_ids: Iterable[int],
) -> Tuple[float, float, float]:
    instrument_set = set(instrument_ids)
    filtered = [pos for pos in positions if _safe_int(pos.get("ExchangeInstrumentId")) in instrument_set]
    return calculate_mtm(filtered, ltp_map)


def calculate_strategy_mtm(
    strategy_positions: Iterable[dict],
    ltp_map: Dict[int, float],
) -> Tuple[float, float, float]:
    """Calculate strategy MTM using exit_price if available (realized), else LTP (unrealized)."""
    realized = 0.0
    unrealized = 0.0
    for pos in strategy_positions:
        instrument_id = _safe_int(pos.get("instrument_id"))
        if instrument_id == 0:
            continue
        entry_price = _safe_float(pos.get("entry_price"))
        quantity = _safe_int(pos.get("quantity"))
        exit_price = pos.get("exit_price")
        
        if exit_price is not None:
            exit_price = _safe_float(exit_price)
            realized += (exit_price - entry_price) * quantity
        else:
            ltp = ltp_map.get(instrument_id)
            if ltp is not None:
                unrealized += (ltp - entry_price) * quantity
    
    total = realized + unrealized
    return realized, unrealized, total
