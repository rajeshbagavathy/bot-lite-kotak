#!/usr/bin/env python3
"""
Validation script to verify API parameter ordering
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import NIFTY, SENSEX

def test_api_params():
    print("=" * 70)
    print("API Parameter Validation")
    print("=" * 70)
    
    print("\n✓ NIFTY API Parameters:")
    print(f"  get_option_symbol() parameters:")
    print(f"    1. exchangeSegment (numeric): {NIFTY.option_ltp_segment} (expected: 2)")
    print(f"    2. series (string):           '{NIFTY.option_exchange_segment}' (expected: 'OPTIDX')")
    print(f"    3. symbol:                    '{NIFTY.fno_symbol}' (expected: 'NIFTY')")
    
    assert NIFTY.option_ltp_segment == 2, "NIFTY exchangeSegment should be 2"
    assert NIFTY.option_exchange_segment == "OPTIDX", "NIFTY series should be 'OPTIDX'"
    assert NIFTY.fno_symbol == "NIFTY", "NIFTY symbol should be 'NIFTY'"
    
    print("\n✓ SENSEX API Parameters:")
    print(f"  get_option_symbol() parameters:")
    print(f"    1. exchangeSegment (numeric): {SENSEX.option_ltp_segment} (expected: 12)")
    print(f"    2. series (string):           '{SENSEX.option_exchange_segment}' (expected: 'IO')")
    print(f"    3. symbol:                    '{SENSEX.fno_symbol}' (expected: 'SENSEX')")
    
    assert SENSEX.option_ltp_segment == 12, "SENSEX exchangeSegment should be 12"
    assert SENSEX.option_exchange_segment == "IO", "SENSEX series should be 'IO'"
    assert SENSEX.fno_symbol == "SENSEX", "SENSEX symbol should be 'SENSEX'"
    
    print("\n" + "=" * 70)
    print("✅ All API parameter validations passed!")
    print("=" * 70)
    print("\nCorrect API Call Pattern:")
    print("  market.get_option_symbol(")
    print("    exchangeSegment,  # Numeric: 2 for NIFTY, 12 for SENSEX")
    print("    series,           # String: 'OPTIDX' for NSE, 'IO' for BSE")
    print("    symbol,           # String: 'NIFTY' or 'SENSEX'")
    print("    expiryDate,       # String: '10Feb2026'")
    print("    optionType,       # String: 'CE' or 'PE'")
    print("    strikePrice       # Int: 23500, 77000, etc.")
    print("  )")
    print("\nExample for NIFTY 23500 CE:")
    print(f"  get_option_symbol({NIFTY.option_ltp_segment}, '{NIFTY.option_exchange_segment}', '{NIFTY.fno_symbol}', '10Feb2026', 'CE', 23500)")
    print("\nExample for SENSEX 77000 PE:")
    print(f"  get_option_symbol({SENSEX.option_ltp_segment}, '{SENSEX.option_exchange_segment}', '{SENSEX.fno_symbol}', '12Feb2026', 'PE', 77000)")
    print("=" * 70)

if __name__ == "__main__":
    test_api_params()
