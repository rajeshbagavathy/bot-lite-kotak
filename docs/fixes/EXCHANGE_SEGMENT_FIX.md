# Exchange Segment Fix - Summary

## Problem Identified

The bot was using the **wrong exchange segment for fetching spot index LTP**, which is critical for ATM strike calculation. The original `xts-trademanager` codebase uses different segments for:

1. **Spot Index LTP** (NSE/BSE CASH segment) - for calculating ATM strike
2. **Option Strikes LTP** (NSE/BSE FNO segment) - for fetching CE/PE premiums

The simplified bot was incorrectly using the FNO segment (2/12) for **both** spot and option LTP fetches.

## Root Cause

The `IndexConfig` dataclass had `ltp_exchange_segment` field that was ambiguously used for multiple purposes:
- Expiry API calls (correct - should use FNO segment)
- Spot index LTP (incorrect - should use CASH segment)
- Option strikes LTP (correct - should use FNO segment)

## Solution Implemented

### 1. Renamed Field in `config.py`
**Before:**
```python
class IndexConfig:
    ...
    ltp_exchange_segment: int  # Ambiguous - used for both spot and options
```

**After:**
```python
class IndexConfig:
    ...
    spot_exchange_segment: int   # For spot index LTP (NSE CASH / BSE CASH)
    option_ltp_segment: int      # For option strikes LTP (NSE FNO / BSE FNO)
```

### 2. Updated Index Configurations
**NIFTY:**
- `spot_exchange_segment=1` (NSE CASH) - for spot index LTP
- `option_ltp_segment=2` (NSE FNO) - for option strikes LTP

**SENSEX:**
- `spot_exchange_segment=11` (BSE CASH) - for spot index LTP
- `option_ltp_segment=12` (BSE FNO) - for option strikes LTP

### 3. Fixed `xts_client.py`
**`get_spot_ltp()` - Now uses correct segment:**
```python
def get_spot_ltp(self, index_config: IndexConfig) -> Optional[float]:
    instruments = [{
        "exchangeSegment": index_config.spot_exchange_segment,  # ✅ Now uses 1/11
        "exchangeInstrumentID": index_config.spot_instrument_id,
    }]
```

**`get_expiry_dates()` - Updated to use option segment:**
```python
result = self.market.get_expiry_date(
    index_config.option_ltp_segment,  # Uses 2/12 (FNO segment)
    index_config.option_exchange_segment,
    index_config.fno_symbol,
)
```

### 4. Fixed `bot.py`
**MTM monitoring - Uses option segment for positions:**
```python
instruments = [
    {"exchangeSegment": index_config.option_ltp_segment, ...}  # ✅ Uses 2/12
    for pos in positions
]
```

## Verification

Created `test_segments.py` to validate:
```
✓ NIFTY Configuration:
  Spot Exchange Segment (NSE CASH):  1 (expected: 1)
  Option LTP Segment (NSE FNO):       2 (expected: 2)

✓ SENSEX Configuration:
  Spot Exchange Segment (BSE CASH):  11 (expected: 11)
  Option LTP Segment (BSE FNO):       12 (expected: 12)

✅ All segment validations passed!
```

## Impact

### Before Fix
- ❌ Spot LTP fetch would fail or return incorrect data (trying to fetch from FNO segment)
- ❌ ATM calculation would be wrong or fail
- ❌ Strategy execution would fail at strike selection

### After Fix
- ✅ Spot LTP fetches from correct CASH segment (1 for NIFTY, 11 for SENSEX)
- ✅ ATM calculation works correctly
- ✅ Option LTP still fetches correctly from FNO segment (2 for NIFTY, 12 for SENSEX)
- ✅ All API calls use appropriate exchange segments

## Files Modified

1. **[config.py](config.py)** - Renamed field and updated NIFTY/SENSEX configs
2. **[xts_client.py](xts_client.py)** - Fixed `get_spot_ltp()` and `get_expiry_dates()`
3. **[bot.py](bot.py)** - Updated MTM monitoring to use `option_ltp_segment`
4. **[test_segments.py](test_segments.py)** - Created validation script

## Testing Recommendations

1. **Run validation script:**
   ```bash
   python3 test_segments.py
   ```

2. **Test with real API:**
   ```bash
   export DEMO_MODE=False
   python3 bot.py
   ```
   
3. **Check logs for:**
   - "Spot LTP: X" should show realistic NIFTY/SENSEX price (e.g., 23500 for NIFTY)
   - "ATM strike: X" should be properly rounded (e.g., 23500 for NIFTY, 77000 for SENSEX)
   - Strategy execution should proceed without errors

4. **Verify in web UI:**
   - Spot price displayed correctly
   - Strategy strikes are at proper ATM levels

## Reference: Exchange Segment Mapping

| Segment ID | Exchange | Type | Purpose |
|-----------|----------|------|---------|
| 1 | NSE | CASH | NIFTY spot index LTP |
| 2 | NSE | FNO | NIFTY options (expiry, LTP, orders) |
| 11 | BSE | CASH | SENSEX spot index LTP |
| 12 | BSE | FNO | SENSEX options (expiry, LTP, orders) |

## Original Code Reference

The fix aligns with the original `xts-trademanager` implementation:

**[helper.py#L38](../xts-trademanager/helper.py#L38)** - Spot LTP:
```python
def getATM():
    ltp = getLtp(TodayIndexConfig.segment, TodayIndexConfig.instrumentId)  # segment=1 for NIFTY
```

**[helper.py#L120](../xts-trademanager/helper.py#L120)** - Option LTP:
```python
instruments = [{'exchangeSegment': TodayIndexConfig.indexSegment, ...}]  # indexSegment=2 for NIFTY
```

**[settings.py#L313](../xts-trademanager/settings.py#L313)** - Configuration:
```python
indexConfig(
    segment=1,        # NSE CASH for spot
    indexSegment=2,   # NSE FNO for options
    ...
)
```
