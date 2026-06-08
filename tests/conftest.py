"""Shared pytest fixtures for SL protection tests."""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import IndexConfig


@pytest.fixture
def nifty_index_config():
    return IndexConfig(
        name="NIFTY",
        fno_symbol="NIFTY",
        lot_size=65,
        strike_diff=50,
        spot_exchange_segment=1,
        spot_instrument_id=26000,
        option_ltp_segment=2,
        option_exchange_segment="OPTIDX",
        order_exchange_segment="NSEFO",
        tick_size=0.05,
    )


@pytest.fixture
def journal_tmp_path(tmp_path, monkeypatch):
    """Isolated trade journal file for each test."""
    path = str(tmp_path / "trade_journal.jsonl")
    monkeypatch.setenv("TRADE_JOURNAL_PATH", path)
    import trading.journal as jmod

    jmod._journal_path = None
    jmod.init_journal(path)
    return path


@pytest.fixture
def filled_entry_orders():
    return [
        {
            "AppOrderID": 1001,
            "OrderStatus": "Filled",
            "OrderAverageTradedPrice": 150.0,
            "OrderQuantity": 130,
            "OrderQuantityTraded": 130,
            "ExchangeInstrumentID": 12345,
            "TradingSymbol": "NIFTY24APR25000CE",
            "ProductType": "MIS",
        },
        {
            "AppOrderID": 1002,
            "OrderStatus": "Filled",
            "OrderAverageTradedPrice": 145.0,
            "OrderQuantity": 130,
            "OrderQuantityTraded": 130,
            "ExchangeInstrumentID": 67890,
            "TradingSymbol": "NIFTY24APR25000PE",
            "ProductType": "MIS",
        },
    ]


@pytest.fixture
def placed_entry_legs():
    return [
        {"app_order_id": 1001, "tag": "S_TEST_CE", "instrument_id": 12345, "quantity": 130},
        {"app_order_id": 1002, "tag": "S_TEST_PE", "instrument_id": 67890, "quantity": 130},
    ]
