#!/usr/bin/env python3
"""
Comprehensive validation test for all configuration fixes
"""
import sys
from pathlib import Path

# Add parent directory to path to import bot modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import NIFTY, SENSEX, IndexConfig

def test_all_configurations():
    print("=" * 70)
    print("COMPREHENSIVE CONFIGURATION VALIDATION")
    print("=" * 70)
    
    # Test NIFTY
    print("\n🔵 NIFTY Configuration:")
    print("-" * 70)
    print(f"✓ Name: {NIFTY.name}")
    print(f"✓ FNO Symbol: {NIFTY.fno_symbol}")
    print(f"✓ Lot Size: {NIFTY.lot_size} (updated Feb 2026)")
    print(f"✓ Strike Diff: {NIFTY.strike_diff}")
    print(f"✓ Spot Exchange Segment: {NIFTY.spot_exchange_segment} (NSE CASH)")
    print(f"✓ Spot Instrument ID: {NIFTY.spot_instrument_id}")
    print(f"✓ Option LTP Segment: {NIFTY.option_ltp_segment} (NSE FNO)")
    print(f"✓ Option Exchange Segment: '{NIFTY.option_exchange_segment}' (series)")
    print(f"✓ Order Exchange Segment: '{NIFTY.order_exchange_segment}'")
    
    # Validate NIFTY
    assert NIFTY.lot_size == 65, "NIFTY lot size should be 65"
    assert NIFTY.spot_exchange_segment == 1, "NIFTY spot should use segment 1"
    assert NIFTY.option_ltp_segment == 2, "NIFTY options should use segment 2"
    assert NIFTY.option_exchange_segment == "OPTIDX", "NIFTY series should be OPTIDX"
    assert NIFTY.order_exchange_segment == "NSEFO", "NIFTY orders should use NSEFO"
    
    # Test SENSEX
    print("\n🔴 SENSEX Configuration:")
    print("-" * 70)
    print(f"✓ Name: {SENSEX.name}")
    print(f"✓ FNO Symbol: {SENSEX.fno_symbol}")
    print(f"✓ Lot Size: {SENSEX.lot_size}")
    print(f"✓ Strike Diff: {SENSEX.strike_diff}")
    print(f"✓ Spot Exchange Segment: {SENSEX.spot_exchange_segment} (BSE CASH)")
    print(f"✓ Spot Instrument ID: {SENSEX.spot_instrument_id}")
    print(f"✓ Option LTP Segment: {SENSEX.option_ltp_segment} (BSE FNO)")
    print(f"✓ Option Exchange Segment: '{SENSEX.option_exchange_segment}' (series)")
    print(f"✓ Order Exchange Segment: '{SENSEX.order_exchange_segment}'")
    
    # Validate SENSEX
    assert SENSEX.lot_size == 20, "SENSEX lot size should be 20"
    assert SENSEX.spot_exchange_segment == 11, "SENSEX spot should use segment 11"
    assert SENSEX.option_ltp_segment == 12, "SENSEX options should use segment 12"
    assert SENSEX.option_exchange_segment == "IO", "SENSEX series should be IO"
    assert SENSEX.order_exchange_segment == "BSEFO", "SENSEX orders should use BSEFO"
    
    # Verify no option_series field exists
    assert not hasattr(NIFTY, 'option_series'), "option_series field should be removed"
    assert not hasattr(SENSEX, 'option_series'), "option_series field should be removed"
    
    print("\n" + "=" * 70)
    print("✅ ALL VALIDATIONS PASSED!")
    print("=" * 70)
    
    # Summary
    print("\n📊 Summary:")
    print("-" * 70)
    print("Exchange Segments:")
    print(f"  NIFTY  - Spot: {NIFTY.spot_exchange_segment} (CASH) | Options: {NIFTY.option_ltp_segment} (FNO)")
    print(f"  SENSEX - Spot: {SENSEX.spot_exchange_segment} (CASH) | Options: {SENSEX.option_ltp_segment} (FNO)")
    print("\nLot Sizes:")
    print(f"  NIFTY:  {NIFTY.lot_size} (8 lots = {8 * NIFTY.lot_size} qty)")
    print(f"  SENSEX: {SENSEX.lot_size} (8 lots = {8 * SENSEX.lot_size} qty)")
    print("\nAPI Parameter Order (get_option_symbol):")
    print(f"  1. exchangeSegment (numeric): {NIFTY.option_ltp_segment} or {SENSEX.option_ltp_segment}")
    print(f"  2. series (string): '{NIFTY.option_exchange_segment}' or '{SENSEX.option_exchange_segment}'")
    print(f"  3. symbol: '{NIFTY.fno_symbol}' or '{SENSEX.fno_symbol}'")
    print("  4. expiryDate, optionType, strikePrice")
    
    print("\n" + "=" * 70)
    print("🎯 Configuration is production-ready!")
    print("=" * 70)

if __name__ == "__main__":
    test_all_configurations()
