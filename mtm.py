import math
from typing import Dict, Iterable, List, Optional, Tuple


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


def calculate_mtm_from_kotak_broker_pnl(positions: Iterable[dict]) -> Optional[Tuple[float, float, float]]:
    """Sum Kotak ``rPNL``/``uPNL`` only when **every** open position row includes them."""
    pos_list = list(positions)
    open_rows = [p for p in pos_list if _safe_int(p.get("Quantity")) != 0]
    if not open_rows:
        return None
    realized = 0.0
    unrealized = 0.0
    for pos in open_rows:
        if "KotakRealizedMtm" not in pos or "KotakUnrealizedMtm" not in pos:
            return None
        realized += _safe_float(pos.get("KotakRealizedMtm"))
        unrealized += _safe_float(pos.get("KotakUnrealizedMtm"))
    return realized, unrealized, realized + unrealized


def calculate_mtm_kotak_amounts(
    positions: Iterable[dict], ltp_map: Dict[int, float]
) -> Optional[Tuple[float, float, float]]:
    """
    Kotak Positions.md: PnL = (sellAmt - buyAmt) + netQty * LTP * multiplier.

    Works without avgPrc; preferred over XTS-style OpenBuy/Sell weighting for Kotak FNO.
    """
    pos_list = list(positions)
    open_rows = [
        p
        for p in pos_list
        if _safe_int(p.get("Quantity")) != 0
        or abs(_safe_float(p.get("KotakSellAmount")) - _safe_float(p.get("KotakBuyAmount"))) > 0.01
    ]
    if not open_rows:
        return None
    if not any(
        _safe_float(p.get("KotakBuyAmount")) != 0.0 or _safe_float(p.get("KotakSellAmount")) != 0.0
        for p in open_rows
    ):
        return None

    realized = 0.0
    unrealized = 0.0
    for pos in open_rows:
        buy_amt = _safe_float(pos.get("KotakBuyAmount"))
        sell_amt = _safe_float(pos.get("KotakSellAmount"))
        qty = _safe_int(pos.get("Quantity"))
        mult = _safe_int(pos.get("Multiplier"), 1)
        iid = _safe_int(pos.get("ExchangeInstrumentId"))
        booked = sell_amt - buy_amt
        ltp = ltp_map.get(iid) if iid else None
        if qty == 0 or ltp is None:
            realized += booked
            continue
        realized += booked
        unrealized += float(qty) * float(ltp) * mult

    return realized, unrealized, realized + unrealized


def calculate_portfolio_mtm_from_strategies(
    strategies: Iterable[dict],
    broker_positions: Iterable[dict],
    ltp_map: Dict[int, float],
) -> Tuple[float, float, float]:
    """
    Portfolio MTM = sum(OPEN strategy straddle legs at entry) + broker hedge/other legs.

    Strategy legs are authoritative for straddle P&L (broker may net or omit rows).
    """
    strat_realized = 0.0
    strat_unrealized = 0.0
    strat_total = 0.0
    strat_instruments: set[int] = set()

    for strategy in strategies:
        if strategy.get("status") != "OPEN":
            continue
        for iid in strategy.get("instrument_ids") or []:
            try:
                strat_instruments.add(int(iid))
            except (TypeError, ValueError):
                continue
        s_positions = strategy.get("positions") or []
        for p in s_positions:
            try:
                iid = int(p.get("instrument_id") or 0)
            except (TypeError, ValueError):
                iid = 0
            if iid:
                strat_instruments.add(iid)
        if s_positions:
            sr, su, st = calculate_strategy_mtm(s_positions, ltp_map)
            strat_realized += sr
            strat_unrealized += su
            strat_total += st

    hedge_positions: List[dict] = []
    for pos in broker_positions:
        try:
            iid = int(pos.get("ExchangeInstrumentId") or 0)
        except (TypeError, ValueError):
            iid = 0
        if iid in strat_instruments:
            continue
        qty = _safe_int(pos.get("Quantity"))
        booked = _safe_float(pos.get("KotakSellAmount")) - _safe_float(pos.get("KotakBuyAmount"))
        if qty == 0 and abs(booked) < 0.01:
            continue
        hedge_positions.append(pos)

    if hedge_positions:
        hedge_mtm = calculate_mtm_kotak_amounts(hedge_positions, ltp_map)
        if hedge_mtm is None:
            hedge_mtm = calculate_mtm(hedge_positions, ltp_map)
        hr, hu, ht = hedge_mtm
    else:
        hr = hu = ht = 0.0

    return strat_realized + hr, strat_unrealized + hu, strat_total + ht


def mtm_position_breakdown(
    positions: Iterable[dict], ltp_map: Dict[int, float]
) -> List[dict]:
    """Per-position MTM diagnostics for logging / debug API."""
    rows: List[dict] = []
    for pos in positions:
        qty = _safe_int(pos.get("Quantity"))
        if qty == 0:
            continue
        iid = _safe_int(pos.get("ExchangeInstrumentId"))
        buy_amt = _safe_float(pos.get("KotakBuyAmount"))
        sell_amt = _safe_float(pos.get("KotakSellAmount"))
        ltp = ltp_map.get(iid) if iid else None
        mult = _safe_int(pos.get("Multiplier"), 1)
        booked = sell_amt - buy_amt
        if ltp is not None and qty != 0:
            total = booked + float(qty) * float(ltp) * mult
        else:
            total = booked
        rows.append(
            {
                "symbol": pos.get("TradingSymbol"),
                "instrument_id": iid,
                "qty": qty,
                "buy_amt": buy_amt,
                "sell_amt": sell_amt,
                "ltp": ltp,
                "booked": booked,
                "total_pnl": total,
                "avg": pos.get("AveragePrice"),
            }
        )
    return rows


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
