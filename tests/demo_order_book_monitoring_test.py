#!/usr/bin/env python
"""
Comprehensive demo test for order book monitoring system.

Validates that:
1. SL order status from XTS order book is monitored correctly
2. Position status syncs with SL order status (FILLED → Closed)
3. Multiple strategies on same instrument don't interfere
4. Position exit_price is captured only when SL is FILLED
"""

import sys
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import bot
from bot import _sync_sl_order_status_and_capture_exits, _check_all_positions_closed

def print_section(title):
    """Print section header."""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)

def print_position_status(strategy, prefix=""):
    """Pretty print position status."""
    print(f"\n{prefix}Strategy: {strategy['name']} (Status: {strategy['status']})")
    for i, pos in enumerate(strategy.get('positions', []) or []):
        status = "✅ CLOSED" if pos.get('exit_price') else "⏳ OPEN"
        exit_price = pos.get('exit_price') or 'N/A'
        print(f"  [{i}] {pos['symbol']}: {status} | Exit Price: {exit_price}")

def demo_scenario_1_basic_sl_fill():
    """Scenario 1: Basic SL fill with position closure."""
    print_section("SCENARIO 1: Basic SL Fill → Position Closure")
    
    # Setup: Strategy with one CE/PE position
    strategy = {
        "name": "S0921",
        "status": "OPEN",
        "db_id": 1,
        "sl_orders": [
            {"tag": "S0921_SL_CE_19900", "app_order_id": "app_1"},
            {"tag": "S0921_SL_PE_19900", "app_order_id": "app_2"},
        ],
        "sl_tag_map": {
            "S0921_SL_CE_19900": 12345,
            "S0921_SL_PE_19900": 12346,
        },
        "positions": [
            {
                "instrument_id": 12345,
                "quantity": -260,
                "entry_price": 150.0,
                "exit_price": None,
                "symbol": "NIFTY19900CE",
            },
            {
                "instrument_id": 12346,
                "quantity": -260,
                "entry_price": 100.0,
                "exit_price": None,
                "symbol": "NIFTY19900PE",
            },
        ],
    }
    
    print("\n📋 INITIAL STATE:")
    print_position_status(strategy, "  ")
    
    # Mock XTS order book: both SL orders are FILLED
    mock_client = MagicMock()
    mock_client.get_order_book.return_value = [
        {
            "OrderUniqueIdentifier": "S0921_SL_CE_19900",
            "OrderStatus": "FILLED",
            "OrderAverageTradedPrice": 145.50,
        },
        {
            "OrderUniqueIdentifier": "S0921_SL_PE_19900",
            "OrderStatus": "FILLED",
            "OrderAverageTradedPrice": 95.75,
        },
    ]
    
    print("\n📡 XTS ORDER BOOK:")
    print("  S0921_SL_CE_19900: FILLED @ 145.50")
    print("  S0921_SL_PE_19900: FILLED @ 95.75")
    
    # Execute order book sync
    with patch('bot.logger'), patch('bot.update_position_exit'):
        _sync_sl_order_status_and_capture_exits(mock_client, strategy)
    
    print("\n📋 AFTER ORDER BOOK SYNC:")
    print_position_status(strategy, "  ")
    
    # Verify all positions are closed
    all_closed = _check_all_positions_closed(strategy)
    print(f"\n✅ All Positions Closed: {all_closed}")
    
    if strategy["positions"][0]["exit_price"] == 145.50 and \
       strategy["positions"][1]["exit_price"] == 95.75 and \
       all_closed:
        print("✅ SCENARIO 1 PASSED: Positions correctly closed via SL fill")
        return True
    else:
        print("❌ SCENARIO 1 FAILED: Position closure not captured correctly")
        return False

def demo_scenario_2_pending_sl():
    """Scenario 2: SL order still PENDING."""
    print_section("SCENARIO 2: SL Order PENDING → Positions Remain OPEN")
    
    strategy = {
        "name": "S0955",
        "status": "OPEN",
        "db_id": 2,
        "sl_orders": [
            {"tag": "S0955_SL_CE_19900", "app_order_id": "app_3"},
        ],
        "sl_tag_map": {
            "S0955_SL_CE_19900": 12345,
        },
        "positions": [
            {
                "instrument_id": 12345,
                "quantity": -260,
                "entry_price": 150.0,
                "exit_price": None,
                "symbol": "NIFTY19900CE",
            },
        ],
    }
    
    print("\n📋 INITIAL STATE:")
    print_position_status(strategy, "  ")
    
    # Mock XTS order book: SL order is PENDING
    mock_client = MagicMock()
    mock_client.get_order_book.return_value = [
        {
            "OrderUniqueIdentifier": "S0955_SL_CE_19900",
            "OrderStatus": "PENDING",
            "OrderAverageTradedPrice": 0,
        },
    ]
    
    print("\n📡 XTS ORDER BOOK:")
    print("  S0955_SL_CE_19900: PENDING")
    
    # Execute order book sync
    with patch('bot.logger'), patch('bot.update_position_exit'):
        _sync_sl_order_status_and_capture_exits(mock_client, strategy)
    
    print("\n📋 AFTER ORDER BOOK SYNC:")
    print_position_status(strategy, "  ")
    print(f"  SL Status: {strategy['positions'][0].get('sl_status', 'N/A')}")
    
    # Verify position is still open
    all_closed = _check_all_positions_closed(strategy)
    print(f"\n✅ All Positions Closed: {all_closed}")
    
    if strategy["positions"][0]["exit_price"] is None and \
       not all_closed and \
       strategy["positions"][0].get("sl_status") == "WAITING":
        print("✅ SCENARIO 2 PASSED: Position correctly remains OPEN with SL pending")
        return True
    else:
        print("❌ SCENARIO 2 FAILED: Position status not handled correctly")
        return False

def demo_scenario_3_multi_strategy_isolation():
    """Scenario 3: Two strategies on same instrument, only one SL fills."""
    print_section("SCENARIO 3: Multi-Strategy Isolation (Same Instrument CE/PE)")
    
    strategy_1 = {
        "name": "S0921",
        "status": "OPEN",
        "db_id": 1,
        "sl_orders": [
            {"tag": "S0921_SL_CE_19900", "app_order_id": "app_1"},
        ],
        "sl_tag_map": {
            "S0921_SL_CE_19900": 12345,
        },
        "positions": [
            {
                "instrument_id": 12345,
                "quantity": -260,
                "entry_price": 150.0,
                "exit_price": None,
                "symbol": "NIFTY19900CE",
            },
        ],
    }
    
    strategy_2 = {
        "name": "S0955",
        "status": "OPEN",
        "db_id": 2,
        "sl_orders": [
            {"tag": "S0955_SL_CE_19900", "app_order_id": "app_2"},
        ],
        "sl_tag_map": {
            "S0955_SL_CE_19900": 12345,  # SAME INSTRUMENT!
        },
        "positions": [
            {
                "instrument_id": 12345,
                "quantity": -260,
                "entry_price": 150.0,
                "exit_price": None,
                "symbol": "NIFTY19900CE",
            },
        ],
    }
    
    print("\n📋 INITIAL STATE:")
    print("  Both S0921 and S0955 hold -260 qty on NIFTY19900CE")
    print("  (Broker shows -520 qty in aggregate)")
    print_position_status(strategy_1, "  S0921: ")
    print_position_status(strategy_2, "  S0955: ")
    
    # Mock XTS order book: only S0921's SL is FILLED
    mock_client = MagicMock()
    mock_client.get_order_book.return_value = [
        {
            "OrderUniqueIdentifier": "S0921_SL_CE_19900",
            "OrderStatus": "FILLED",
            "OrderAverageTradedPrice": 145.50,
        },
        {
            "OrderUniqueIdentifier": "S0955_SL_CE_19900",
            "OrderStatus": "PENDING",
            "OrderAverageTradedPrice": 0,
        },
    ]
    
    print("\n📡 XTS ORDER BOOK:")
    print("  S0921_SL_CE_19900: FILLED @ 145.50")
    print("  S0955_SL_CE_19900: PENDING")
    
    # Execute order book sync for both strategies
    with patch('bot.logger'), patch('bot.update_position_exit'):
        _sync_sl_order_status_and_capture_exits(mock_client, strategy_1)
        _sync_sl_order_status_and_capture_exits(mock_client, strategy_2)
    
    print("\n📋 AFTER ORDER BOOK SYNC:")
    print_position_status(strategy_1, "  S0921: ")
    print_position_status(strategy_2, "  S0955: ")
    
    s1_closed = _check_all_positions_closed(strategy_1)
    s2_closed = _check_all_positions_closed(strategy_2)
    print(f"\n  S0921 All Closed: {s1_closed}")
    print(f"  S0955 All Closed: {s2_closed}")
    
    # Verify S0921 is closed but S0955 remains open
    if strategy_1["positions"][0]["exit_price"] == 145.50 and \
       s1_closed and \
       strategy_2["positions"][0]["exit_price"] is None and \
       not s2_closed:
        print("\n✅ SCENARIO 3 PASSED: Multi-strategy isolation working correctly")
        print("   S0921 closed independently without affecting S0955")
        return True
    else:
        print("\n❌ SCENARIO 3 FAILED: Multi-strategy isolation broken")
        return False

def demo_scenario_4_rejected_sl():
    """Scenario 4: SL order REJECTED (position exposed)."""
    print_section("SCENARIO 4: SL Order REJECTED (Position Exposed)")
    
    strategy = {
        "name": "S1005",
        "status": "OPEN",
        "db_id": 3,
        "sl_orders": [
            {"tag": "S1005_SL_CE_19900", "app_order_id": "app_5"},
        ],
        "sl_tag_map": {
            "S1005_SL_CE_19900": 12345,
        },
        "positions": [
            {
                "instrument_id": 12345,
                "quantity": -260,
                "entry_price": 150.0,
                "exit_price": None,
                "symbol": "NIFTY19900CE",
            },
        ],
    }
    
    print("\n📋 INITIAL STATE:")
    print_position_status(strategy, "  ")
    
    # Mock XTS order book: SL order REJECTED
    mock_client = MagicMock()
    mock_client.get_order_book.return_value = [
        {
            "OrderUniqueIdentifier": "S1005_SL_CE_19900",
            "OrderStatus": "REJECTED",
            "OrderAverageTradedPrice": 0,
        },
    ]
    
    print("\n📡 XTS ORDER BOOK:")
    print("  S1005_SL_CE_19900: REJECTED ⚠️")
    
    # Execute order book sync
    with patch('bot.logger') as mock_logger, patch('bot.update_position_exit'):
        _sync_sl_order_status_and_capture_exits(mock_client, strategy)
    
    print("\n📋 AFTER ORDER BOOK SYNC:")
    print_position_status(strategy, "  ")
    print(f"  SL Status: {strategy['positions'][0].get('sl_status', 'N/A')}")
    
    # Verify position is exposed
    all_closed = _check_all_positions_closed(strategy)
    print(f"\n⚠️  All Positions Closed: {all_closed}")
    print(f"⚠️  Logged Warning: {mock_logger.warning.called}")
    
    if strategy["positions"][0]["exit_price"] is None and \
       not all_closed and \
       strategy["positions"][0].get("sl_status") == "REJECTED" and \
       mock_logger.warning.called:
        print("\n✅ SCENARIO 4 PASSED: SL rejection correctly detected and logged")
        return True
    else:
        print("\n❌ SCENARIO 4 FAILED: SL rejection not handled correctly")
        return False

def main():
    """Run all demo scenarios."""
    print("\n" + "=" * 80)
    print(" ORDER BOOK MONITORING SYSTEM - DEMO TEST SUITE")
    print("=" * 80)
    
    results = []
    
    # Run scenarios
    results.append(("Scenario 1: Basic SL Fill", demo_scenario_1_basic_sl_fill()))
    results.append(("Scenario 2: Pending SL", demo_scenario_2_pending_sl()))
    results.append(("Scenario 3: Multi-Strategy Isolation", demo_scenario_3_multi_strategy_isolation()))
    results.append(("Scenario 4: Rejected SL", demo_scenario_4_rejected_sl()))
    
    # Print summary
    print_section("TEST SUMMARY")
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"  {status}: {name}")
    
    print(f"\n  Total: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED - Order book monitoring system ready for production!")
        return 0
    else:
        print(f"\n❌ {total - passed} test(s) failed - Review implementation")
        return 1

if __name__ == "__main__":
    sys.exit(main())
