# Order Book Monitoring Implementation

## Overview

The order book is the **single source of truth** for position closure status. We monitor XTS order book every 3 seconds to detect when:
- SL orders are FILLED (position closed)
- SL orders are PENDING (still waiting)
- SL orders are REJECTED/CANCELLED (position exposed)

This replaces the previous approach of matching broker position quantities, which failed with multi-strategy execution.

---

## The Problem We Solved

**Multi-Strategy Conflict:** When multiple strategies execute the same ATM strike simultaneously:

```
Broker Position Aggregation:
- S0921: -260 qty CE (closed -260)
- S0955: -260 qty CE (closed -260)
- Broker shows: -520 qty CE (one aggregated position)

OLD Approach (BROKEN):
→ Try to buy 520 qty to close (matching broker's aggregated position)
→ Closes BOTH strategies simultaneously
→ No way to distinguish which SL order belongs to which strategy
Result: ❌ Cross-strategy interference

NEW Approach (ORDER BOOK):
→ Fetch XTS order book with strategy-specific SL order tags
→ S0921_SL_CE has status FILLED → S0921's CE is closed
→ S0955_SL_CE has status FILLED → S0955's CE is closed
→ Each determined independently
Result: ✅ Complete isolation
```

---

## Implementation

### New Function: `_sync_sl_order_status_and_capture_exits()`

**Location:** [bot.py](../../bot.py#L308)

**Purpose:** Monitor order book and capture exit prices when SLs are filled.

#### Function Signature
```python
def _sync_sl_order_status_and_capture_exits(
    client: XTSClient, 
    strategy: dict
) -> None:
    """Monitor SL order status in XTS order book and capture exit prices."""
```

#### Logic Flow

```python
# 1. Check if strategy is open and has SL orders
if strategy['state'] != 'OPEN' or not strategy['sl_orders']:
    return

# 2. Fetch current order book from XTS
try:
    order_book = client.get_order_book()
except Exception as e:
    logger.error(f"Failed to fetch order book: {e}")
    return

# 3. For each position in strategy
for i, position in enumerate(strategy['positions']):
    # Find the corresponding SL order
    sl_order = strategy['sl_orders'][i]
    app_order_id = str(sl_order['app_order_id'])
    
    # 4. Look up order in order book
    order_detail = order_book.get(app_order_id, {})
    if not order_detail:
        logger.warning(f"SL order {app_order_id} not in order book")
        continue
    
    # 5. Check order status
    status = order_detail.get("OrderStatus", "").upper()
    
    # 6. Action based on status
    if status == "FILLED":
        # SL hit, position closed, capture exit price
        exit_price = float(order_detail.get("OrderFilledPrice", 0))
        if exit_price > 0:
            position['exit_price'] = exit_price
            position['exit_time'] = order_detail.get("FillTime")
            logger.info(f"Position {i} exit captured at {exit_price}")
    
    elif status == "PENDING":
        # Still waiting for SL to hit
        position['sl_status'] = "WAITING"
    
    elif status in ("REJECTED", "CANCELLED"):
        # SL order failed, position exposed
        position['sl_status'] = status
        logger.warning(f"SL order {app_order_id} {status} - position exposed!")
```

### Helper Function: `_check_all_positions_closed()`

**Location:** [bot.py](../../bot.py#L414)

**Purpose:** Verify if ALL positions in a strategy have closed.

```python
def _check_all_positions_closed(strategy: dict) -> bool:
    """Return True if all positions have exit_price set."""
    positions = strategy.get('positions', [])
    if not positions:
        return True  # No positions, considered closed
    
    return all(pos.get('exit_price') is not None for pos in positions)
```

---

## Data Structure

### Order Book Response Format

```python
order_book = {
    "1001": {
        "AppOrderID": "1001",
        "OrderStatus": "FILLED",        # Status: NEW, FILLED, REJECTED, CANCELLED
        "OrderFilledPrice": "5125.00",  # Execution price
        "FillTime": "2024-02-19 10:15:30",
        "OrderQuantity": "260",
        "TradingSymbol": "NIFTY19900CE"
    },
    "1002": {
        "AppOrderID": "1002",
        "OrderStatus": "PENDING",       # Still waiting
        "OrderFilledPrice": "0",
        "FillTime": None
    },
    ...
}
```

### Strategy State with SL Status

```python
strategy = {
    "name": "S0921",
    "state": "OPEN",
    "positions": [
        {
            "quantity": -260,
            "symbol": "NIFTY19900CE",
            "entry_price": 95.50,
            "exit_price": 105.25,        # Set when SL FILLED
            "exit_time": "2024-02-19 10:15:30",
            "sl_status": "FILLED"
        },
        {
            "quantity": -260,
            "symbol": "NIFTY19900PE",
            "entry_price": 92.00,
            "exit_price": None,           # Still open
            "exit_time": None,
            "sl_status": "WAITING"        # Waiting for SL
        }
    ],
    "sl_orders": [
        {"app_order_id": 1001, "tag": "S0921_SL_CE_19900"},
        {"app_order_id": 1002, "tag": "S0921_SL_PE_19900"}
    ]
}
```

---

## Integration with Monitoring Loop

**Call sequence (every 3 seconds):**

```python
def _monitor_mtm(client, index_config, strategies):
    for strategy in strategies:
        # STEP 1: Sync SL order status from order book (ORDER BOOK AS SOURCE OF TRUTH)
        _sync_sl_order_status_and_capture_exits(client, strategy)
        
        # STEP 2: Also sync from broker (detect manual closures)
        _sync_strategy_positions_from_broker(client, strategy)
        
        # STEP 3: Check if all positions closed
        if _check_all_positions_closed(strategy):
            strategy['state'] = 'CLOSED'
            continue
        
        # STEP 4: Calculate MTM with updated exit prices
        mtm = calculate_mtm(strategy)
        
        # STEP 5: Log and check thresholds
        logger.info(f"{strategy['name']} MTM: {mtm}")
```

---

## Status Meanings

| Status | Meaning | Action |
|--------|---------|--------|
| **NEW** | Order placed, waiting for fill | Monitor for FILLED or REJECTED |
| **FILLED** | SL triggered, position closed | Capture exit_price, mark as CLOSED |
| **REPLACED** | Order re-opened after partial close | Re-evaluate and potentially modify |
| **PENDING** | Processing at exchange | Continue monitoring |
| **REJECTED** | Exchange rejected the order | Position exposed, log warning |
| **CANCELLED** | Order cancelled (manual or automatic) | Position exposed, log warning |
| **MISSING** | Order not yet in book | Skip and continue (might appear later) |

---

## Multi-Strategy Isolation

### Scenario: Two Strategies, Same Strike

```
S0921: Sells -260 qty CE 19900 @ 09:20
       CE SL: tag = "S0921_SL_CE_19900", app_order_id = 1001

S0955: Sells -260 qty CE 19900 @ 10:01
       CE SL: tag = "S0955_SL_CE_19900", app_order_id = 2001

Market: CE 19900 rises to 100 (SL level)

Order Book (after both SLs hit):
{
    "1001": {"OrderStatus": "FILLED", "OrderFilledPrice": "100.00"},
    "2001": {"OrderStatus": "FILLED", "OrderFilledPrice": "100.00"}
}

Monitoring:
→ S0921: Finds order 1001 → FILLED → exit_price = 100.00 ✅
→ S0955: Finds order 2001 → FILLED → exit_price = 100.00 ✅
→ Both tracked independently, no confusion
```

---

## Error Handling

### Order Book Fetch Failure
```python
try:
    order_book = client.get_order_book()
except Exception as e:
    logger.error(f"Failed to fetch order book: {e}")
    return  # Skip for this cycle, retry next cycle
```

### Order Not in Book
```python
order_detail = order_book.get(app_order_id, {})
if not order_detail:
    logger.warning(f"Order {app_order_id} not in order book yet")
    continue  # Skip this order, might appear on next fetch
```

### Invalid Price Data
```python
exit_price = float(order_detail.get("OrderFilledPrice", 0))
if exit_price <= 0:
    logger.warning(f"Invalid exit price {exit_price}, skipping")
    continue  # Don't capture invalid data
```

### DEMO_MODE
```python
if client is None:  # DEMO_MODE
    # Simulate order book monitoring
    for position in strategy.get('positions', []):
        position['exit_price'] = position['entry_price']  # Mock exit
        position['sl_status'] = 'FILLED'
```

---

## Test Coverage

### Unit Tests: 10 Tests

**TestSyncSLOrderStatusAndCaptureExits:**
- ✅ Skips if strategy not OPEN
- ✅ Skips if no SL orders
- ✅ Handles DEMO_MODE (client is None)
- ✅ Handles order book fetch errors
- ✅ Captures exit_price when SL FILLED
- ✅ Sets sl_status when SL PENDING
- ✅ Warns on SL REJECTED
- ✅ Warns on SL CANCELLED
- ✅ Skips when order not in order book
- ✅ Validates exit prices > 0

### Integration Tests: 4 Scenarios

**tests/demo_order_book_monitoring_test.py:**
- Scenario 1: Basic SL fill → Position closure captured
- Scenario 2: Pending SL → Position remains open
- Scenario 3: Multi-strategy isolation (same CE/PE, different strategies)
- Scenario 4: SL rejection → Position exposed with warning

---

## Performance Considerations

1. **Fetch Frequency:** Every 3 seconds (configurable in `_monitor_mtm()`)
   - Balances responsiveness vs API rate limits
   - For 4 strategies: ~4 API calls per fetch + 4 MTM calculations

2. **Order Book Size:** Typically 50-100 orders per symbol
   - Lookups are O(1) via dict/map
   - No performance issues observed

3. **Memory:** Stores position states and SL order mappings
   - ~1KB per strategy, negligible overhead

4. **API Rate Limits:** XTS allows ~10+ calls/second
   - Our usage: ~2-3 calls/second
   - Well within limits

---

## Configuration

No explicit configuration needed. The monitoring runs automatically:

```python
# bot.py - _monitor_mtm() runs every 3 seconds
schedule.every(3).seconds.do(
    _monitor_mtm,
    client=client,
    index_config=index_config,
    strategies=strategies
)
```

To adjust frequency, edit [bot.py](../../bot.py):
```python
schedule.every(5).seconds.do(...)  # Change to 5 seconds
```

---

## Troubleshooting

### Exit prices not captured
1. Check `bot.log` for fetch errors
2. Verify order book returns "FILLED" status
3. Confirm exit price > 0 in order response
4. Run `tests/test_expiry_debug.py` to verify API

### Missing order in order book
1. Order might not have been placed successfully
2. Check XTS order ID was captured during entry
3. Verify SL order tag is correct

### Position shows CLOSED but should be OPEN
1. Check if all positions have exit_price set
2. Verify individual SL fills didn't happen unexpectedly
3. Review order book history in `bot.log`

---

## See Also

- [STRATEGY_CLOSURE.md](STRATEGY_CLOSURE.md) - How we modify SL orders to close
- [../ARCHITECTURE.md](../ARCHITECTURE.md) - Overall system design
- [../DEPLOYMENT.md](../DEPLOYMENT.md) - Production setup
