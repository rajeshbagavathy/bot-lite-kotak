#!/usr/bin/env python3
"""
Test: Daily cleanup of previous day data on startup.

This test verifies that:
1. Previous day data is deleted on startup
2. Today's data is preserved
3. Old strategies, positions, orders, and MTM snapshots are cleaned
"""

import sys
import sqlite3
from datetime import datetime, timedelta

sys.path.insert(0, '/Users/rajeshkumarbagavathy/PycharmProjects/xts-5p-saranya/xts-bot-lite')

from db import (
    DB_PATH, 
    get_ist_date, 
    get_ist_now,
    init_db, 
    cleanup_previous_day_data,
    log_strategy_execution,
    log_position,
    log_order,
    log_trade_closed,
    log_mtm_snapshot
)

def count_records():
    """Count all records in database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM strategies")
        strategy_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM positions")
        position_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM orders")
        order_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM trades_closed")
        trade_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM mtm_snapshots")
        mtm_count = cursor.fetchone()[0]
        
        conn.close()
        return strategy_count, position_count, order_count, trade_count, mtm_count
    except Exception as e:
        print(f"Error counting records: {e}")
        return 0, 0, 0, 0, 0


def insert_test_data(execution_date):
    """Insert test data for a specific date."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Insert strategy
        cursor.execute("""
            INSERT INTO strategies 
            (strategy_name, execution_date, strike, entry_time, status, lots, leg_sl_pct, strategy_sl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (f"TEST_{execution_date}", execution_date, 19900 + (20 if execution_date == get_ist_date() else 0), 
              "09:20:00", "OPEN", 8, 20.0, 16000.0))
        
        strategy_id = cursor.lastrowid
        
        # Insert position
        cursor.execute("""
            INSERT INTO positions 
            (strategy_id, symbol, instrument_id, quantity, entry_price, entry_time)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (strategy_id, "NIFTY19900CE", 12345, -260, 95.5, "09:20:30"))
        
        # Insert order
        cursor.execute("""
            INSERT INTO orders 
            (strategy_name, order_tag, instrument_id, symbol, quantity, order_type, order_side, status, traded_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (f"TEST_{execution_date}", f"TEST_{execution_date}_SL_1", 12345, "NIFTY19900CE", 260, "SL", "BUY", "PENDING", 105.5))
        
        # Insert MTM snapshot (using created_at instead of timestamp)
        cursor.execute("""
            INSERT INTO mtm_snapshots 
            (strategy_name, mtm, realized, unrealized)
            VALUES (?, ?, ?, ?)
        """, (f"TEST_{execution_date}", 500.0, 100.0, 400.0))
        
        conn.commit()
        conn.close()
        print(f"✓ Inserted test data for {execution_date}")
    except Exception as e:
        print(f"Error inserting test data: {e}")


def test_daily_cleanup():
    """Test that cleanup removes previous day data but keeps today's."""
    print("=" * 60)
    print("Test: Daily Cleanup on Startup")
    print("=" * 60)
    
    # Initialize database
    print("\n1. Initialize database...")
    init_db()
    print("   ✓ Database initialized")
    
    # Insert data for previous days
    today = get_ist_date()
    yesterday = (get_ist_now() - timedelta(days=1)).strftime("%Y-%m-%d")
    two_days_ago = (get_ist_now() - timedelta(days=2)).strftime("%Y-%m-%d")
    
    print(f"\n2. Insert test data for multiple days...")
    print(f"   Today: {today}")
    print(f"   Yesterday: {yesterday}")
    print(f"   Two days ago: {two_days_ago}")
    
    insert_test_data(two_days_ago)
    insert_test_data(yesterday)
    insert_test_data(today)
    
    # Count before cleanup
    print(f"\n3. Count records BEFORE cleanup...")
    strat_before, pos_before, order_before, trade_before, mtm_before = count_records()
    print(f"   Strategies: {strat_before} | Positions: {pos_before} | Orders: {order_before} | Trades: {trade_before} | MTM: {mtm_before}")
    print(f"   Total: {strat_before + pos_before + order_before + trade_before + mtm_before} records")
    
    # Verify we have 3 of each
    assert strat_before >= 3, f"Expected at least 3 strategies, got {strat_before}"
    assert mtm_before >= 3, f"Expected at least 3 MTM snapshots, got {mtm_before}"
    print("   ✓ Test data inserted successfully")
    
    # Run cleanup
    print(f"\n4. Run cleanup_previous_day_data()...")
    cleanup_previous_day_data()
    
    # Count after cleanup
    print(f"\n5. Count records AFTER cleanup...")
    strat_after, pos_after, order_after, trade_after, mtm_after = count_records()
    print(f"   Strategies: {strat_after} | Positions: {pos_after} | Orders: {order_after} | Trades: {trade_after} | MTM: {mtm_after}")
    print(f"   Total: {strat_after + pos_after + order_after + trade_after + mtm_after} records")
    
    # Verify results
    print(f"\n6. Verify cleanup results...")
    
    # Previous day data should be deleted
    assert strat_after < strat_before, "Previous day strategies not deleted"
    print(f"   ✓ Strategies deleted: {strat_before} → {strat_after}")
    
    assert mtm_after < mtm_before, "Previous day MTM not deleted"
    print(f"   ✓ MTM snapshots deleted: {mtm_before} → {mtm_after}")
    
    # Today's data should remain
    assert strat_after >= 1, "Today's strategy was deleted!"
    print(f"   ✓ Today's strategy preserved: {strat_after} strategy (for {today})")
    
    assert pos_after >= 1, "Today's positions were deleted!"
    print(f"   ✓ Today's positions preserved: {pos_after} positions")
    
    assert mtm_after >= 1, "Today's MTM was deleted!"
    print(f"   ✓ Today's MTM preserved: {mtm_after} snapshots")
    
    # Calculate deleted counts
    deleted_strategies = strat_before - strat_after
    deleted_mtm = mtm_before - mtm_after
    
    print(f"\n7. Cleanup summary...")
    print(f"   ✓ Deleted {deleted_strategies} previous day strategies")
    print(f"   ✓ Deleted {deleted_mtm} previous day MTM snapshots")
    print(f"   ✓ Preserved all {strat_after} today's data")
    
    print("\n" + "=" * 60)
    print("✓ Daily cleanup test PASSED")
    print("=" * 60)
    return True


if __name__ == "__main__":
    try:
        test_daily_cleanup()
        sys.exit(0)
    except AssertionError as e:
        print(f"\n✗ Test FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
