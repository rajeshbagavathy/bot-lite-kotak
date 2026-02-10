#!/usr/bin/env python3
"""
Validation script to verify exchange segment configuration
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import NIFTY, SENSEX

def test_segments():
    print("=" * 60)
    print("Exchange Segment Configuration Validation")
    print("=" * 60)
    
    # NIFTY validation
    print("\n✓ NIFTY Configuration:")
    print(f"  Spot Exchange Segment (NSE CASH):  {NIFTY.spot_exchange_segment} (expected: 1)")
    print(f"  Option LTP Segment (NSE FNO):       {NIFTY.option_ltp_segment} (expected: 2)")
    print(f"  Spot Instrument ID:                 {NIFTY.spot_instrument_id}")
    
    assert NIFTY.spot_exchange_segment == 1, "NIFTY spot should use segment 1 (NSE CASH)"
    assert NIFTY.option_ltp_segment == 2, "NIFTY options should use segment 2 (NSE FNO)"
    
    # SENSEX validation
    print("\n✓ SENSEX Configuration:")
    print(f"  Spot Exchange Segment (BSE CASH):  {SENSEX.spot_exchange_segment} (expected: 11)")
    print(f"  Option LTP Segment (BSE FNO):       {SENSEX.option_ltp_segment} (expected: 12)")
    print(f"  Spot Instrument ID:                 {SENSEX.spot_instrument_id}")
    
    assert SENSEX.spot_exchange_segment == 11, "SENSEX spot should use segment 11 (BSE CASH)"
    assert SENSEX.option_ltp_segment == 12, "SENSEX options should use segment 12 (BSE FNO)"
    
    print("\n" + "=" * 60)
    print("✅ All segment validations passed!")
    print("=" * 60)
    print("\nExchange Segment Usage:")
    print("  - Spot Index LTP (ATM calculation): Uses spot_exchange_segment")
    print("    → NIFTY: Segment 1 (NSE CASH)")
    print("    → SENSEX: Segment 11 (BSE CASH)")
    print("  - Option Strikes LTP: Uses option_ltp_segment")
    print("    → NIFTY: Segment 2 (NSE FNO)")
    print("    → SENSEX: Segment 12 (BSE FNO)")
    print("  - Expiry API: Uses option_ltp_segment")
    print("  - Order Placement: Uses order_exchange_segment (NSEFO/BSEFO)")
    print("=" * 60)

if __name__ == "__main__":
    test_segments()
