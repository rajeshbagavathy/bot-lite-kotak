"""Order book helpers."""
from typing import Any, Dict, List


def get_filled_orders(order_book: List[dict], app_order_ids: List[int]) -> List[dict]:
    id_set = {int(x) for x in (app_order_ids or []) if x is not None}
    return [
        order
        for order in order_book
        if int(order.get("AppOrderID") or 0) in id_set
        and str(order.get("OrderStatus", "")).replace(" ", "").upper()
        in ("FILLED", "PARTIALLYFILLED")
        and float(order.get("OrderAverageTradedPrice") or 0) > 0
    ]


def order_book_status_is_filled(status_raw: str | None) -> bool:
    s = (status_raw or "").replace(" ", "").replace("_", "").upper()
    return s in (
        "FILLED",
        "COMPLETE",
        "COMPLETED",
        "TRADED",
        "CLOSED",
        "EXECUTED",
        "FULLYTRADED",
    )
