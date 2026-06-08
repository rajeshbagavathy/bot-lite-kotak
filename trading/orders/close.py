"""Position close and cancel helpers."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from trading.journal import Phase, record as journal

logger = logging.getLogger("xts-bot-lite")


def positions_exposure_for_instruments(
    broker_positions: List[dict], instrument_ids: List[int]
) -> Dict[int, int]:
    want = {int(i) for i in instrument_ids if i is not None}
    out: Dict[int, int] = {}
    for pos in broker_positions or []:
        try:
            iid = int(pos.get("ExchangeInstrumentId") or pos.get("exchangeInstrumentId") or 0)
            qty = int(pos.get("Quantity") or pos.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        if iid in want and qty != 0:
            out[iid] = qty
    return out


def cancel_order_logged(
    client: Any,
    strategy_name: str,
    app_order_id: int,
    tag: str,
    *,
    flow: str,
    severity: str = "WARNING",
) -> None:
    """Cancel with journal attribution (which code path cancelled)."""
    try:
        client.cancel_order(app_order_id, tag)
        journal(
            Phase.ORDER_CANCELLED,
            strategy_name,
            f"Cancel order {app_order_id} ({flow})",
            severity=severity,
            app_order_id=app_order_id,
            tag=tag,
            flow=flow,
        )
    except Exception as e:
        journal(
            Phase.ORDER_CANCELLED,
            strategy_name,
            f"Cancel failed for {app_order_id} ({flow}): {e}",
            severity="ERROR",
            app_order_id=app_order_id,
            tag=tag,
            flow=flow,
        )
        logger.exception("Failed to cancel order %s (%s)", app_order_id, flow)


def close_positions_for_instruments(
    client: Any,
    index_config,
    positions: List[dict],
    instrument_ids: List[int],
    *,
    strategy_name: str = "",
    flow: str = "close_positions",
) -> None:
    from trading.compat import resolve

    place_close = resolve("_place_close_order", _place_close_order)
    for pos in positions:
        try:
            iid = int(pos.get("ExchangeInstrumentId") or pos.get("exchangeInstrumentId") or 0)
        except (TypeError, ValueError):
            continue
        if iid in instrument_ids:
            journal(
                Phase.SAFETY_FLATTEN,
                strategy_name,
                f"Closing position instrument {iid} ({flow})",
                instrument_id=iid,
                flow=flow,
            )
            place_close(client, index_config, pos, "CLOSE")


def _place_close_order(client: Any, index_config, pos: dict, tag_prefix: str) -> None:
    """Minimal close — full implementation remains in bot until migrated."""
    try:
        instrument_id = int(pos.get("ExchangeInstrumentId") or pos.get("exchangeInstrumentId"))
        qty = abs(int(pos.get("Quantity") or pos.get("quantity") or 0))
        side = client.interactive.TRANSACTION_TYPE_BUY if int(pos.get("Quantity") or 0) < 0 else client.interactive.TRANSACTION_TYPE_SELL
        tag = f"{tag_prefix}_{instrument_id}_{int(time.time())}"
        client.place_market_order(
            index_config=index_config,
            instrument_id=instrument_id,
            order_side=side,
            quantity=qty,
            tag=tag,
            product_type=pos.get("ProductType") or client.interactive.PRODUCT_MIS,
        )
    except Exception:
        logger.exception("place_close_order failed for %s", pos)
