"""
End-to-end integration tests for entry → SL → verify → PROTECTED pipeline.

Uses a simulated broker (no live XTS credentials). Validates journal phase
sequence and state transitions for happy path and safety flatten paths.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import trading.context as ctx
import trading.journal as journal_mod
from trading.journal import Phase, init_journal, read_tail
from trading.orders.lifecycle import (
    complete_entry_with_sl_protection,
    enforce_open_strategy_sl_invariant,
)
from trading.state_bridge import update_strategy


class _InteractiveStub:
    TRANSACTION_TYPE_BUY = "BUY"
    TRANSACTION_TYPE_SELL = "SELL"
    PRODUCT_MIS = "MIS"
    ORDER_TYPE_STOPLIMIT = "STOPLIMIT"


class FakeBrokerClient:
    """Minimal in-memory broker for SL protection integration tests."""

    def __init__(self):
        self.interactive = _InteractiveStub()
        self._next_id = 1000
        self.entry_orders: Dict[int, dict] = {}
        self.sl_orders: Dict[int, dict] = {}
        self.positions: List[dict] = []
        self.close_orders: List[dict] = []
        self._entry_fill_delay = 0  # polls before entries show filled
        self._poll_count = 0
        self.sl_verify_fail = False

    def _nid(self) -> int:
        self._next_id += 1
        return self._next_id

    def place_market_order(self, index_config, instrument_id, order_side, quantity, tag, product_type, ltp=None, slippage_pct=None):
        oid = self._nid()
        self.entry_orders[oid] = {
            "AppOrderID": oid,
            "ExchangeInstrumentID": instrument_id,
            "OrderStatus": "Pending",
            "OrderAverageTradedPrice": 0,
            "OrderQuantity": quantity,
            "OrderSide": order_side,
            "tag": tag,
        }
        return oid

    def place_sl_order(self, index_config, instrument_id, order_side, quantity, limit_price, stop_price, tag, product_type):
        oid = self._nid()
        status = "Rejected" if self.sl_verify_fail else "New"
        self.sl_orders[oid] = {
            "AppOrderID": oid,
            "ExchangeInstrumentID": instrument_id,
            "OrderStatus": status,
            "OrderQuantity": quantity,
            "OrderUniqueIdentifier": tag,
        }
        return oid

    def get_order_book(self) -> List[dict]:
        self._poll_count += 1
        if self._poll_count > self._entry_fill_delay:
            for e in self.entry_orders.values():
                if e["OrderStatus"] == "Pending":
                    e["OrderStatus"] = "Filled"
                    e["OrderAverageTradedPrice"] = 150.0
                    iid = e["ExchangeInstrumentID"]
                    qty = -abs(int(e["OrderQuantity"])) if e["OrderSide"] == "SELL" else int(e["OrderQuantity"])
                    self._set_position(iid, qty)
        return list(self.entry_orders.values()) + list(self.sl_orders.values())

    def get_positions(self) -> List[dict]:
        return list(self.positions)

    def _set_position(self, instrument_id: int, quantity: int) -> None:
        for p in self.positions:
            if int(p["ExchangeInstrumentId"]) == instrument_id:
                p["Quantity"] = quantity
                return
        self.positions.append(
            {
                "ExchangeInstrumentId": instrument_id,
                "Quantity": quantity,
                "ProductType": "MIS",
            }
        )

    def seed_entry_orders(self, placed_entry: List[dict], *, filled: bool = False) -> None:
        """Register entry orders the lifecycle will poll by AppOrderID."""
        for p in placed_entry:
            oid = int(p["app_order_id"])
            iid = int(p["instrument_id"])
            qty = int(p.get("quantity") or 65)
            status = "Filled" if filled else "Pending"
            avg = 150.0 if filled else 0
            self.entry_orders[oid] = {
                "AppOrderID": oid,
                "ExchangeInstrumentID": iid,
                "OrderStatus": status,
                "OrderAverageTradedPrice": avg,
                "OrderQuantity": qty,
                "OrderQuantityTraded": qty,
                "OrderSide": "SELL",
                "ProductType": "MIS",
                "TradingSymbol": f"INST_{iid}",
            }
            if filled:
                self._set_position(iid, -qty)

    def place_close_via_flatten(self, instrument_ids: List[int]) -> None:
        for iid in instrument_ids:
            self._set_position(iid, 0)
            self.close_orders.append({"instrument_id": iid})


@pytest.fixture
def broker():
    return FakeBrokerClient()


@pytest.fixture(autouse=True)
def reset_state(journal_tmp_path, monkeypatch):
    ctx.STRATEGY_STATE.clear()
    journal_mod._journal_path = None
    init_journal(journal_tmp_path)
    monkeypatch.setattr(
        "trading.orders.lifecycle.time.sleep",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "trading.orders.sl.time.sleep",
        lambda *_a, **_k: None,
    )


def _phases_from_journal() -> List[str]:
    return [e["phase"] for e in read_tail(50)]


def test_e2e_entry_to_protected(broker, nifty_index_config, placed_entry_legs):
    """Full happy path: fills → SL placed → verified → PROTECTED."""
    broker._entry_fill_delay = 0
    broker.seed_entry_orders(placed_entry_legs)
    strategy = {"name": "INT_S1", "db_id": 0, "status": "OPEN"}

    result = complete_entry_with_sl_protection(
        broker,
        nifty_index_config,
        strategy,
        placed_entry_legs,
        leg_sl_pct=20.0,
        leg_target_pct=30.0,
    )

    assert result.ok is True
    assert len(result.sl_orders) == 2
    assert len(result.positions) == 2
    assert all(p["exit_price"] is None for p in result.positions)
    assert all(p["target_price"] > 0 for p in result.positions)

    phases = _phases_from_journal()
    assert Phase.ENTRY_FILL_WAIT.value in phases
    assert Phase.ENTRY_FILLED.value in phases
    assert Phase.SL_SENT.value in phases
    assert Phase.SL_VERIFY.value in phases
    assert Phase.PROTECTED.value in phases
    assert Phase.SAFETY_FLATTEN.value not in phases


def test_e2e_sl_verify_fail_triggers_flatten(broker, nifty_index_config, placed_entry_legs, monkeypatch):
    """If SL orders are rejected, bot must flatten and abort."""
    broker.sl_verify_fail = True
    broker._entry_fill_delay = 0
    broker.seed_entry_orders(placed_entry_legs)
    strategy = {"name": "INT_S2", "db_id": 0, "status": "OPEN"}
    updates = []
    monkeypatch.setattr(
        "trading.orders.lifecycle.update_strategy",
        lambda name, **kw: updates.append((name, kw)),
    )
    monkeypatch.setattr(
        "trading.orders.lifecycle.close_positions_for_instruments",
        lambda client, cfg, positions, iids, **kw: broker.place_close_via_flatten(iids),
    )

    result = complete_entry_with_sl_protection(
        broker,
        nifty_index_config,
        strategy,
        placed_entry_legs,
        leg_sl_pct=20.0,
        leg_target_pct=30.0,
    )

    assert result.ok is False
    assert result.flattened is True
    assert any(u[1].get("status") == "ERROR" for u in updates)
    phases = _phases_from_journal()
    assert Phase.SAFETY_FLATTEN.value in phases
    assert Phase.SL_REJECTED.value in phases or Phase.SL_VERIFY.value in phases


def test_e2e_fill_timeout_with_exposure_flattens(broker, nifty_index_config, placed_entry_legs, monkeypatch):
    """Exposure without confirmed fills must flatten (original production bug class)."""
    broker._entry_fill_delay = 999  # never confirm fills in order book
    broker._set_position(12345, -130)
    broker._set_position(67890, -130)
    strategy = {"name": "INT_S3", "db_id": 0, "status": "OPEN"}

    monkeypatch.setattr(
        "trading.orders.lifecycle.close_positions_for_instruments",
        lambda client, cfg, positions, iids, **kw: broker.place_close_via_flatten(iids),
    )

    result = complete_entry_with_sl_protection(
        broker,
        nifty_index_config,
        strategy,
        placed_entry_legs,
        leg_sl_pct=20.0,
        leg_target_pct=30.0,
    )

    assert result.ok is False
    assert result.flattened is True
    phases = _phases_from_journal()
    assert Phase.ENTRY_FILL_TIMEOUT.value in phases
    assert Phase.SAFETY_FLATTEN.value in phases
    assert Phase.STRATEGY_ABORT.value in phases


def test_e2e_monitor_invariant_catches_missing_sl(broker, nifty_index_config, monkeypatch):
    """Background monitor must flatten OPEN strategy with exposure but no SL in state."""
    strategy = {
        "name": "INT_S4",
        "status": "OPEN",
        "instrument_ids": [12345, 67890],
        "sl_orders": [],
        "positions": [],
    }
    positions = [
        {"ExchangeInstrumentId": 12345, "Quantity": -130, "ProductType": "MIS"},
        {"ExchangeInstrumentId": 67890, "Quantity": -130, "ProductType": "MIS"},
    ]
    updates = []
    monkeypatch.setattr(
        "trading.orders.lifecycle.update_strategy",
        lambda name, **kw: updates.append((name, kw)),
    )
    monkeypatch.setattr(
        "trading.orders.lifecycle.close_positions_for_instruments",
        lambda client, cfg, positions, iids, **kw: broker.place_close_via_flatten(iids),
    )

    triggered = enforce_open_strategy_sl_invariant(
        broker, nifty_index_config, strategy, positions, order_book=[]
    )

    assert triggered is True
    assert any(u[1].get("status") == "ERROR" for u in updates)
    assert Phase.MONITOR_INVARIANT.value in _phases_from_journal()
    assert len(broker.close_orders) >= 1


def test_e2e_journal_api_readable(journal_tmp_path):
    """Journal lines are valid JSON and include required fields."""
    from trading.journal import record

    record(Phase.ENTRY_SENT, "INT_S5", "test event", lots=2)
    events = read_tail(5, strategy="INT_S5")
    assert len(events) == 1
    assert events[0]["strategy"] == "INT_S5"
    assert events[0]["phase"] == Phase.ENTRY_SENT.value
    with open(journal_tmp_path, encoding="utf-8") as fh:
        line = fh.readline()
        parsed = json.loads(line)
        assert "ts" in parsed
        assert "message" in parsed
