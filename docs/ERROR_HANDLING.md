# Error Handling: No Expiry Found

## Overview

When the XTS API returns no expiry data for NIFTY or SENSEX (typically outside market hours), the bot now gracefully handles this error instead of crashing. The error is displayed prominently on the UI, and the bot automatically retries until expiry data becomes available.

## Scenario

**What happens:**
- Bot starts but XTS API has no expiry data available
- `_pick_index_and_expiry()` raises `RuntimeError: No expiries found for NIFTY or SENSEX`
- **Before:** App crashed, showing full traceback
- **After:** Error displayed on UI, bot keeps running and retries automatically

## How It Works

### 1. Error Handling in main()

When `_pick_index_and_expiry()` fails:

```python
try:
    index_config, expiry = _pick_index_and_expiry(client)
except RuntimeError as e:
    logger.error(f"Failed to pick index and expiry: {e}")
    index_config = None
    expiry = None
    # Continue execution instead of crashing
```

### 2. User-Friendly Error Message

Error is stored in application state:

```python
set_index_error("No expiries found for NIFTY or SENSEX. Please check market hours (09:15-15:30).")
```

### 3. UI Display

Dashboard shows error banner at the top:

```
⚠️ Status
No expiries found for NIFTY or SENSEX. Please check market hours (09:15-15:30).
```

**Features:**
- Red/orange banner highlighting (CSS styled)
- Auto-refreshes every 3 seconds
- Shows when expiry becomes available

### 4. Automatic Retry

Background thread retries periodically:

```python
_retry_pick_expiry(client, auth)
```

**Retry Logic:**
- Starts at 10-second intervals
- Increases by 5 seconds each attempt
- Maxes out at 60 seconds
- Updates UI with retry attempt number and current time
- Logs each attempt

**When expiry is found:**
- Updates index state with name and expiry
- Schedules trading jobs
- Restores any open strategies from database
- Updates UI with success message
- Bot becomes fully operational

## Code Changes

### state.py

**New function:** `set_index_error(error_message: str)`
- Sets error message in `STATE["index"]["error"]`
- Clears index name and expiry
- Thread-safe with lock

**Updated function:** `set_index(name, expiry)`
- Now clears error message when successful

**Updated:** `init_state()`
- Added "error" field to index state object

### bot.py

**New function:** `_retry_pick_expiry(client, auth)`
- Periodically attempts to pick index and expiry
- Updates UI with progress
- Automatically schedules jobs when successful
- Restores strategies from database

**Updated:** `main()`
- Wrapped `_pick_index_and_expiry()` in try-except
- Sets error state on failure
- Continues with graceful degradation
- Starts retry thread if needed

**Updated:** Import statement
- Added `set_index_error` to imports

### ui.py

**Updated:** Meta-info display logic
- Checks for `idx.error` field
- Shows error banner if error exists
- Displays progress messages

## UI Behavior

### When Error Occurs

```
┌─────────────────────────────────────────┐
│ ⚠️ Status                               │
│ No expiries found for NIFTY or SENSEX. │
│ Please check market hours (09:15-15:30) │
├─────────────────────────────────────────┤
│ Index: -                                 │
│ Expiry: -                                │
│ Spot: -                                  │
│ Portfolio MTM: 0.00                      │
│ Available Margin: -                      │
└─────────────────────────────────────────┘
```

### During Retry

Error message updates:
```
⚠️ Status
Still waiting for expiry data... (Attempt 3)
```

### When Successful

Error message clears, normal display resumes:
```
Index: NIFTY
Expiry: 20DEC2024
Spot: 23950.25
Portfolio MTM: 0.00
Available Margin: 250000.00
```

## Logging

**Error Phase:**
```
2026-02-19 05:20:24 - ERROR - Failed to pick index and expiry: No expiries found for NIFTY or SENSEX
⏳ Waiting for expiry data... Current time: 05:20:24 (Market hours: 09:15-15:30)
```

**Retry Phase:**
```
2026-02-19 05:20:35 - INFO - 🔄 Retry 1: Checking for expiry data...
2026-02-19 05:20:46 - INFO - 🔄 Retry 2: Checking for expiry data...
⏳ Retrying in 20s... Current time: 05:20:46 (Market hours: 09:15-15:30)
```

**Success Phase:**
```
2026-02-19 09:16:00 - INFO - 🔄 Retry 28: Checking for expiry data...
2026-02-19 09:16:00 - INFO - ✓ Expiry found! Index: NIFTY | Expiry: 20DEC2024
2026-02-19 09:16:00 - INFO - ✓ Bot is now operational
```

## Key Features

✅ **No Crash** - App continues running gracefully  
✅ **Clear Error** - User sees exactly what's wrong  
✅ **Auto-Recover** - Retries until data available  
✅ **UI Updates** - Real-time status on dashboard  
✅ **Logs Details** - Debugging info available  
✅ **Demo Mode** - Still works in demo/test mode  
✅ **Thread-Safe** - All state updates use locks  

## Testing

### Trigger Error Manually

To test this behavior outside market hours:
```bash
python bot.py
```

The bot will:
1. Attempt to fetch expiry
2. Show error on UI
3. Keep retrying in background
4. Auto-recover when market hours arrive

### Check Logs

```bash
tail -f bot.log
```

Monitor retry attempts and status messages.

## User Actions

If you see the error message:

1. **Check Market Hours:** Is it outside 09:15-15:30 IST?
2. **Wait for Market:** Bot will auto-recover at market opening
3. **Check API:** Are XTS API credentials correct?
4. **Restart if Needed:** Manual restart works fine

## Configuration

No configuration needed. The retry behavior is hardcoded:
- Initial retry: 10 seconds
- Increment: 5 seconds per attempt
- Maximum: 60 seconds

To adjust these values, edit `_retry_pick_expiry()` in bot.py:
```python
retry_interval = 10  # Start with 10 seconds
max_interval = 60    # Max out at 60 seconds
```

## See Also

- [ARCHITECTURE.md](ARCHITECTURE.md) - System design overview
- [docs/QUICKSTART.md](QUICKSTART.md) - Getting started guide
