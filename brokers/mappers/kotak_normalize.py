"""Map Kotak Neo API payloads to XTS-shaped dicts expected by bot.py."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def _safe_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _safe_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _kotak_ord_st_to_order_status(ord_st: Optional[str], stat: Optional[str]) -> str:
    raw = (ord_st or stat or "").strip().lower()
    key = raw.replace(" ", "").replace("_", "")
    m = {
        "open": "NEW",
        "new": "NEW",
        "pending": "PENDING",
        "triggerpending": "NEW",
        "replaced": "REPLACED",
        "complete": "FILLED",
        "traded": "FILLED",
        "filled": "FILLED",
        "cancelled": "CANCELLED",
        "rejected": "REJECTED",
        "partiallyfilled": "PARTIALLYFILLED",
    }
    return m.get(key, key.upper() if key else "UNKNOWN")


def _trns_tp_to_side(tp: Optional[str]) -> str:
    t = (tp or "").strip().upper()
    if t == "B":
        return "BUY"
    if t == "S":
        return "SELL"
    return t or "BUY"


def _prc_tp_to_xts_order_type(prc_tp: Optional[str]) -> str:
    u = (prc_tp or "").strip().upper()
    if u in ("L",):
        return "LIMIT"
    if u in ("MKT",):
        return "MARKET"
    if u in ("SL",):
        return "STOPLIMIT"
    if u in ("SL-M", "SLM"):
        return "STOPLIMIT"
    return u or "LIMIT"


def kotak_order_to_normalized(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Kotak order_report row -> keys used in bot.py (XTS-style).

    See Kotak Order_report.md sample fields: nOrdNo, ordSt, stat, trdSym, tok, trnsTp,
    prcTp, qty, fldQty, unFldSz, trgPrc, prc, vldt, prod, GuiOrdId, vendorCode, ...
    """
    n_ord = row.get("nOrdNo")
    app_id = _safe_int(n_ord)
    if app_id is None and n_ord is not None:
        # Rare non-numeric id: stable hash fallback (avoid collision with real ids in practice)
        app_id = abs(hash(str(n_ord))) % (10**15)

    tok = row.get("tok")
    iid = _safe_int(tok)
    if iid is None:
        iid = 0

    qty = _safe_int(row.get("qty")) or 0
    fld = _safe_int(row.get("fldQty")) or 0
    unfld = row.get("unFldSz")
    unfld_i = _safe_int(unfld)
    if unfld_i is None:
        unfld_i = max(qty - fld, 0)

    prc = _safe_float(row.get("prc"))
    trg = _safe_float(row.get("trgPrc"))
    avg = _safe_float(row.get("avgPrc"))

    tag = (
        row.get("GuiOrdId")
        or row.get("vendorCode")
        or row.get("strategyCode")
        or row.get("symOrdId")
        or ""
    )
    if tag in ("", "NA", "na", None):
        tag = ""

    return {
        "AppOrderID": app_id,
        "OrderUniqueIdentifier": str(tag) if tag else str(app_id),
        "OrderStatus": _kotak_ord_st_to_order_status(row.get("ordSt"), row.get("stat")),
        "OrderSide": _trns_tp_to_side(row.get("trnsTp")),
        "ExchangeInstrumentID": iid,
        "ExchangeInstrumentId": iid,
        "TradingSymbol": row.get("trdSym") or row.get("sym") or "",
        "OrderQuantity": qty,
        "OrderQuantityTraded": fld,
        "LeavesQuantity": unfld_i,
        "OrderAverageTradedPrice": avg if avg is not None else 0.0,
        "OrderPrice": prc,
        "LimitPrice": prc,
        "OrderStopPrice": trg,
        "StopPrice": trg,
        "TriggerPrice": trg,
        "ProductType": (row.get("prod") or "MIS").upper(),
        "TimeInForce": (row.get("vldt") or "DAY").upper(),
        "OrderDisclosedQuantity": _safe_int(row.get("dscQty")) or 0,
        "OrderType": _prc_tp_to_xts_order_type(row.get("prcTp")),
    }


def _instrument_token_from_kotak_row(row: Dict[str, Any]) -> int:
    """Kotak token / pSymbol for quotes (``tok`` is often empty on cash-style rows)."""
    for k in ("tok", "pSymbol", "instrument_token", "instrumentToken", "scrip_token", "wToken"):
        v = row.get(k)
        if v is None or str(v).strip() == "":
            continue
        i = _safe_int(v)
        if i is not None and i != 0:
            return i
    return 0


def _kotak_price_scale(row: Dict[str, Any]) -> float:
    mult = _safe_float(row.get("multiplier")) or 1.0
    gen_num = _safe_float(row.get("genNum")) or 1.0
    gen_den = _safe_float(row.get("genDen")) or 1.0
    prc_num = _safe_float(row.get("prcNum")) or 1.0
    prc_den = _safe_float(row.get("prcDen")) or 1.0
    if gen_den == 0 or prc_den == 0:
        return mult
    return mult * (gen_num / gen_den) * (prc_num / prc_den)


def _kotak_row_is_open_position(row: Dict[str, Any]) -> bool:
    """Prefer ``posFlg=true`` rows; skip fill-only rows when flagged."""
    flag = row.get("posFlg")
    if flag is None or str(flag).strip() == "":
        return True
    return str(flag).strip().lower() in ("true", "1", "y", "yes")


def _kotak_row_qty_amounts(row: Dict[str, Any]) -> Dict[str, float]:
    """
    Kotak positions qty/amount totals in **exchange units (shares)**, not lots.

    See Kotak Positions.md: net = (cfBuy+flBuy) - (cfSell+flSell); amounts from buyAmt/sellAmt.
    """
    buy_qty = float((_safe_int(row.get("cfBuyQty")) or 0) + (_safe_int(row.get("flBuyQty")) or 0))
    sell_qty = float((_safe_int(row.get("cfSellQty")) or 0) + (_safe_int(row.get("flSellQty")) or 0))
    buy_amt = float(_safe_float(row.get("cfBuyAmt")) or 0.0) + float(_safe_float(row.get("buyAmt")) or 0.0)
    sell_amt = float(_safe_float(row.get("cfSellAmt")) or 0.0) + float(_safe_float(row.get("sellAmt")) or 0.0)
    net_qty = buy_qty - sell_qty
    # Legacy / alternate payloads
    for k in ("netQty", "netqty", "ntQty", "NetQty"):
        v = _safe_int(row.get(k))
        if v is not None and (buy_qty == 0 and sell_qty == 0):
            net_qty = float(v)
            if net_qty > 0:
                buy_qty = abs(net_qty)
            elif net_qty < 0:
                sell_qty = abs(net_qty)
            break
    if buy_qty == 0 and sell_qty == 0:
        q = _safe_int(row.get("qty"))
        if q is not None and q != 0:
            net_qty = float(q)
            if net_qty > 0:
                buy_qty = abs(net_qty)
            else:
                sell_qty = abs(net_qty)
    return {
        "buy_qty": buy_qty,
        "sell_qty": sell_qty,
        "buy_amt": buy_amt,
        "sell_amt": sell_amt,
        "net_qty": net_qty,
    }


def _kotak_weighted_avg_price(total_amt: float, total_qty: float, scale: float) -> float:
    denom = total_qty * scale
    if denom <= 0:
        return 0.0
    return total_amt / denom


def kotak_positions_to_normalized(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Normalize Kotak positions() ``data`` list to XTS positionList-like dicts.

    Aggregates by (trdSym, prod). Uses Kotak buy/sell amounts for average price
    (``avgPrc`` is often absent on FNO rows — without it MTM was computed as ~-qty*LTP).
    """
    buckets: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        if not _kotak_row_is_open_position(row):
            continue
        sym = row.get("trdSym") or row.get("sym") or ""
        prod = (row.get("prod") or row.get("prdCode") or "MIS").upper()
        key = (sym, prod)
        qa = _kotak_row_qty_amounts(row)
        rpnl = _safe_float(row.get("rPNL") or row.get("rpnl") or row.get("RPNL"))
        upnl = _safe_float(row.get("uPNL") or row.get("upnl") or row.get("UPNL"))
        if key not in buckets:
            buckets[key] = {
                "sym": sym,
                "prod": prod,
                "buy_qty": 0.0,
                "sell_qty": 0.0,
                "buy_amt": 0.0,
                "sell_amt": 0.0,
                "rpnl": 0.0,
                "upnl": 0.0,
                "has_broker_pnl": False,
                "row": row,
            }
        b = buckets[key]
        b["buy_qty"] += qa["buy_qty"]
        b["sell_qty"] += qa["sell_qty"]
        b["buy_amt"] += qa["buy_amt"]
        b["sell_amt"] += qa["sell_amt"]
        if rpnl is not None:
            b["rpnl"] += rpnl
            b["has_broker_pnl"] = True
        if upnl is not None:
            b["upnl"] += upnl
            b["has_broker_pnl"] = True
        b["row"] = row

    out: List[Dict[str, Any]] = []
    for b in buckets.values():
        buy_qty = b["buy_qty"]
        sell_qty = b["sell_qty"]
        net_qty = buy_qty - sell_qty
        row = b["row"]
        row_net = _safe_int(row.get("netQty") or row.get("netqty"))
        if row_net is not None:
            net_qty = float(row_net)
        booked = b["sell_amt"] - b["buy_amt"]
        if net_qty == 0:
            if abs(booked) < 0.01 and not b["has_broker_pnl"]:
                continue
            iid = _instrument_token_from_kotak_row(row)
            rec_closed: Dict[str, Any] = {
                "ExchangeInstrumentId": iid,
                "ExchangeInstrumentID": iid,
                "TradingSymbol": b["sym"],
                "Quantity": 0,
                "ProductType": b["prod"],
                "AveragePrice": 0.0,
                "OpenBuyQuantity": 0,
                "OpenSellQuantity": 0,
                "SumOfTradedQuantityAndPriceBuy": float(b["buy_amt"]),
                "SumOfTradedQuantityAndPriceSell": float(b["sell_amt"]),
                "Multiplier": _safe_int(row.get("multiplier")) or 1,
                "KotakBuyAmount": b["buy_amt"],
                "KotakSellAmount": b["sell_amt"],
                "KotakClosedLeg": True,
            }
            if b["has_broker_pnl"]:
                rec_closed["KotakRealizedMtm"] = b["rpnl"]
                rec_closed["KotakUnrealizedMtm"] = b["upnl"]
            out.append(rec_closed)
            continue
        iid = _instrument_token_from_kotak_row(row)
        scale = _kotak_price_scale(row)
        mult = _safe_int(row.get("multiplier")) or 1
        qtot = int(round(net_qty))
        absq = abs(qtot)

        buy_avg = _kotak_weighted_avg_price(b["buy_amt"], buy_qty, scale)
        sell_avg = _kotak_weighted_avg_price(b["sell_amt"], sell_qty, scale)
        if qtot > 0:
            open_buy, open_sell = absq, 0
            avg = buy_avg or (_safe_float(row.get("buyAvg")) or _safe_float(row.get("avgPrc")) or 0.0)
            sum_buy_px_qty = float(b["buy_amt"]) if b["buy_amt"] > 0 else float(avg) * absq
            sum_sell_px_qty = 0.0
        else:
            open_buy, open_sell = 0, absq
            avg = sell_avg or (_safe_float(row.get("sellAvg")) or _safe_float(row.get("avgPrc")) or 0.0)
            sum_buy_px_qty = 0.0
            sum_sell_px_qty = float(b["sell_amt"]) if b["sell_amt"] > 0 else float(avg) * absq

        rec: Dict[str, Any] = {
            "ExchangeInstrumentId": iid,
            "ExchangeInstrumentID": iid,
            "TradingSymbol": b["sym"],
            "Quantity": qtot,
            "ProductType": b["prod"],
            "AveragePrice": avg,
            "OpenBuyQuantity": open_buy,
            "OpenSellQuantity": open_sell,
            "SumOfTradedQuantityAndPriceBuy": sum_buy_px_qty,
            "SumOfTradedQuantityAndPriceSell": sum_sell_px_qty,
            "Multiplier": mult,
            "KotakBuyAmount": b["buy_amt"],
            "KotakSellAmount": b["sell_amt"],
        }
        if b["has_broker_pnl"]:
            rec["KotakRealizedMtm"] = b["rpnl"]
            rec["KotakUnrealizedMtm"] = b["upnl"]
        out.append(rec)
    return out


def parse_kotak_place_order_n_ord_no(resp: Any) -> Optional[int]:
    if not isinstance(resp, dict):
        return None
    if resp.get("Error") or resp.get("Error Message"):
        return None
    n = resp.get("nOrdNo")
    return _safe_int(n)
