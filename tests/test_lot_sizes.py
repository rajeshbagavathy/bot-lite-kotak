#!/usr/bin/env python3
"""
Validation script to verify lot sizes are correct
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import NIFTY, SENSEX

def test_lot_sizes():
    print("=" * 60)
    print("Lot Size Validation")
    print("=" * 60)
    
    # NIFTY validation
    print("\n✓ NIFTY Configuration:")
    print(f"  Lot Size: {NIFTY.lot_size} (expected: 65)")
    assert NIFTY.lot_size == 65, f"NIFTY lot size should be 65, got {NIFTY.lot_size}"
    
    # SENSEX validation
    print("\n✓ SENSEX Configuration:")
    print(f"  Lot Size: {SENSEX.lot_size} (expected: 20)")
    assert SENSEX.lot_size == 20, f"SENSEX lot size should be 20, got {SENSEX.lot_size}"
    
    print("\n" + "=" * 60)
    print("✅ All lot size validations passed!")
    print("=" * 60)
    print("\nLot Size Summary:")
    print(f"  NIFTY:  65 lots = {65 * NIFTY.lot_size:,} quantity")
    print(f"  SENSEX: 20 lots = {20 * SENSEX.lot_size:,} quantity")
    print("=" * 60)

if __name__ == "__main__":
    test_lot_sizes()
