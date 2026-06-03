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
    m = {
        "open": "NEW",
        "new": "NEW",
        "pending": "PENDING",
        "trigger pending": "NEW",
        "replaced": "REPLACED",
        "complete": "FILLED",
        "traded": "FILLED",
        "filled": "FILLED",
        "cancelled": "CANCELLED",
        "rejected": "REJECTED",
        "partiallyfilled": "PARTIALLYFILLED",
        "partially filled": "PARTIALLYFILLED",
    }
    key = raw.replace(" ", "").replace("_", "")
    return m.get(key, raw.upper() or "UNKNOWN")


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


def _net_qty_from_kotak_row(row: Dict[str, Any]) -> int:
    for k in ("netQty", "netqty", "ntQty", "NetQty", "Qty", "qty"):
        if k in row and row[k] is not None and str(row[k]).strip() != "":
            v = _safe_int(row[k])
            if v is not None:
                return v
    buy = (_safe_int(row.get("cfBuyQty")) or 0) + (_safe_int(row.get("flBuyQty")) or 0)
    sell = (_safe_int(row.get("cfSellQty")) or 0) + (_safe_int(row.get("flSellQty")) or 0)
    lot = _safe_int(row.get("lotSz")) or 1
    if lot > 1:
        buy = buy // lot
        sell = sell // lot
    return buy - sell


def kotak_positions_to_normalized(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Normalize Kotak positions() ``data`` list to XTS positionList-like dicts.

    Aggregates by (trdSym, tok, prod) when multiple rows exist.
    """
    buckets: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        sym = row.get("trdSym") or row.get("sym") or ""
        prod = (row.get("prod") or "MIS").upper()
        # One bucket per contract/symbol (``tok`` is often blank; trdSym encodes FNO leg).
        key = (sym, prod)
        q = _net_qty_from_kotak_row(row)
        if key not in buckets:
            buckets[key] = {
                "sym": sym,
                "prod": prod,
                "qty": 0,
                "row": row,
            }
        buckets[key]["qty"] += q
        # Prefer last row for avg price etc.
        buckets[key]["row"] = row

    out: List[Dict[str, Any]] = []
    for b in buckets.values():
        qtot = int(b["qty"])
        if qtot == 0:
            continue
        row = b["row"]
        iid = _instrument_token_from_kotak_row(row)
        avg = _safe_float(row.get("avgPrc")) or _safe_float(row.get("prc")) or 0.0
        mult = _safe_int(row.get("multiplier")) or 1

        # XTS-style fields required by mtm.calculate_mtm (Open*, SumOfTraded*, Multiplier).
        absq = abs(qtot)
        avg_f = float(avg or 0.0)
        if qtot > 0:
            open_buy, open_sell = absq, 0
            sum_buy_px_qty = avg_f * absq
            sum_sell_px_qty = 0.0
        else:
            open_buy, open_sell = 0, absq
            sum_buy_px_qty = 0.0
            sum_sell_px_qty = avg_f * absq

        out.append(
            {
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
            }
        )
    return out


def parse_kotak_place_order_n_ord_no(resp: Any) -> Optional[int]:
    if not isinstance(resp, dict):
        return None
    if resp.get("Error") or resp.get("Error Message"):
        return None
    n = resp.get("nOrdNo")
    return _safe_int(n)
