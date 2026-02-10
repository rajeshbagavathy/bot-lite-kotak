# API Parameter Fix - Summary

## Issues Fixed

### 1. **Exchange Segment for Spot vs Options** ✅
**Problem:** Bot was using FNO segment (2/12) to fetch spot index LTP instead of CASH segment (1/11)

**Fixed in:**
- Renamed `ltp_exchange_segment` → `option_ltp_segment` for clarity
- `get_spot_ltp()` now uses `spot_exchange_segment` (1 for NIFTY, 11 for SENSEX)
- Option-related calls use `option_ltp_segment` (2 for NIFTY, 12 for SENSEX)

### 2. **get_option_symbol() Parameter Order** ✅
**Problem:** Parameters were swapped - passing string first instead of numeric segment

**Before (INCORRECT):**
```python
self.market.get_option_symbol(
    index_config.option_exchange_segment,  # "OPTIDX" - WRONG position!
    index_config.option_series,             # "NSEFO" - WRONG value!
    ...
)
```

**After (CORRECT):**
```python
self.market.get_option_symbol(
    index_config.option_ltp_segment,        # 2 (numeric) ✅
    index_config.option_exchange_segment,   # "OPTIDX" (series) ✅
    ...
)
```

### 3. **NIFTY Lot Size** ✅
**Problem:** Lot size was 75, current market value is 65

**Fixed:** Updated NIFTY `lot_size` from 75 → 65

### 4. **Removed Redundant Field** ✅
**Problem:** `option_series` field was not used and had incorrect values

**Fixed:** Removed `option_series` from `IndexConfig` dataclass

## Files Modified

1. **[config.py](config.py)**
   - Renamed `ltp_exchange_segment` → `option_ltp_segment`
   - Updated NIFTY lot_size: 75 → 65
   - Removed unused `option_series` field
   - Simplified IndexConfig dataclass

2. **[xts_client.py](xts_client.py)**
   - Fixed `get_spot_ltp()` to use `spot_exchange_segment`
   - Fixed `get_option_instrument_id()` parameter order
   - Updated `get_expiry_dates()` to use `option_ltp_segment`

3. **[bot.py](bot.py)**
   - Updated MTM monitoring to use `option_ltp_segment`

4. **[test_expiry_debug.py](test_expiry_debug.py)**
   - Updated logging to use `option_ltp_segment` instead of removed field

## Current Configuration

### NIFTY
```python
IndexConfig(
    name="NIFTY",
    fno_symbol="NIFTY",
    spot_exchange_segment=1,      # NSE CASH - for spot LTP
    spot_instrument_id=26000,
    strike_diff=50,
    lot_size=65,                  # Updated to current value
    option_ltp_segment=2,         # NSE FNO - for options
    option_exchange_segment="OPTIDX",
    order_exchange_segment="NSEFO",
)
```

### SENSEX
```python
IndexConfig(
    name="SENSEX",
    fno_symbol="SENSEX",
    spot_exchange_segment=11,     # BSE CASH - for spot LTP
    spot_instrument_id=26065,
    strike_diff=100,
    lot_size=20,                  # Already correct
    option_ltp_segment=12,        # BSE FNO - for options
    option_exchange_segment="IO",
    order_exchange_segment="BSEFO",
)
```

## Validation Tests Created

1. **[test_segments.py](test_segments.py)** - Validates exchange segment configuration
2. **[test_lot_sizes.py](test_lot_sizes.py)** - Validates lot sizes
3. **[test_api_params.py](test_api_params.py)** - Validates API parameter ordering

All tests passing ✅

## API Call Reference

### Correct Parameter Order

**Spot LTP (for ATM calculation):**
```python
# NIFTY spot from NSE CASH
get_ltp_map([{"exchangeSegment": 1, "exchangeInstrumentID": 26000}])

# SENSEX spot from BSE CASH
get_ltp_map([{"exchangeSegment": 11, "exchangeInstrumentID": 26065}])
```

**Expiry Dates:**
```python
# NIFTY
get_expiry_date(2, "OPTIDX", "NIFTY")

# SENSEX
get_expiry_date(12, "IO", "SENSEX")
```

**Option Symbol (Instrument ID):**
```python
# NIFTY 23500 CE
get_option_symbol(2, "OPTIDX", "NIFTY", "10Feb2026", "CE", 23500)

# SENSEX 77000 PE
get_option_symbol(12, "IO", "SENSEX", "12Feb2026", "PE", 77000)
```

**Option LTP (for MTM):**
```python
# NIFTY option from NSE FNO
get_ltp_map([{"exchangeSegment": 2, "exchangeInstrumentID": 12345}])

# SENSEX option from BSE FNO
get_ltp_map([{"exchangeSegment": 12, "exchangeInstrumentID": 67890}])
```

**Order Placement:**
```python
# NIFTY
place_order(exchangeSegment="NSEFO", ...)

# SENSEX
place_order(exchangeSegment="BSEFO", ...)
```

## Testing Recommendations

1. Run validation tests:
   ```bash
   python3 test_segments.py
   python3 test_lot_sizes.py
   python3 test_api_params.py
   ```

2. Test with real API (DEMO_MODE=False):
   - Verify spot LTP returns realistic price (~23,500 for NIFTY, ~77,000 for SENSEX)
   - Verify ATM strike calculation is correct
   - Verify CE/PE instrument IDs are fetched successfully
   - Verify orders are placed correctly

3. Check logs for:
   - "Spot LTP: X" showing correct index price
   - "ATM strike: X" properly rounded to strike_diff
   - No API errors in option instrument lookup

## Impact

✅ Spot LTP now fetches from correct CASH segment  
✅ Option instrument lookups use correct parameter order  
✅ NIFTY lot size matches current market standard  
✅ Cleaner configuration without redundant fields  
✅ All API calls aligned with XTS API documentation
