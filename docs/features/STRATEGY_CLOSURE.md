# Strategy Closure & SL Management

## Overview

When a strategy's stop loss (SL) is hit or MTM threshold breached, the bot closes positions by modifying open SL orders to market execution. This ensures:
- ✅ Only OPEN SL orders are modified (no double-closing)
- ✅ Partial closures handled (one leg already filled by individual SL)
- ✅ Multi-strategy isolation (each strategy's SLs independent)
- ✅ Error recovery and logging

---

## How Strategy Closure Works

### The Problem We Solved

**Original Issue:** When multiple strategies held the same strike instruments (e.g., S0921 and S0955 both short NIFTY 19900 CE/PE), closing one strategy would incorrectly try to close ALL strategies' positions:

```
Example:
- S0921: Short -260 qty CE 19900
- S0955: Short -260 qty CE 19900
- Broker sees: -520 qty (aggregated)

OLD Code: "Close everything with -520 qty"
Result: Both strategies forced closed ❌

NEW Code: "Check S0921's SL order status, modify only if NEW"
Result: Only S0921 closed correctly ✅
```

### The Solution: Order Book Monitoring

Each strategy's SL orders have unique tags:
- `S0921_SL_CE_19900` - S0921's CE stop loss
- `S0921_SL_PE_19900` - S0921's PE stop loss
- `S0955_SL_CE_19900` - S0955's CE stop loss (independent)
- `S0955_SL_PE_19900` - S0955's PE stop loss (independent)

When closing S0921:
1. Fetch XTS order book
2. Find S0921's CE SL order → Check status
3. Find S0921's PE SL order → Check status
4. Only modify orders with status = "NEW" or "REPLACED"
5. Skip orders with status = "FILLED" (already closed!)

---

## Implementation: `_close_strategy_via_open_sl_orders()`

**Location:** [bot.py](../../bot.py#L241)

### Function Signature
```python
def _close_strategy_via_open_sl_orders(client: XTSClient, strategy: dict) -> None:
    """Close strategy by modifying open SL orders to market execution."""
```

### Logic Flow

```python
# 1. Fetch current order book from XTS
order_book = client.get_order_book()

# 2. For each SL order stored in strategy
for sl_order in strategy['sl_orders']:
    app_order_id = sl_order['app_order_id']
    
    # 3. Find order details in order book
    order_detail = order_book.get(str(app_order_id), {})
    
    # 4. Check status
    status = order_detail.get("OrderStatus", "").upper()
    
    # 5. Action based on status
    if status in ("NEW", "REPLACED"):
        # Order still pending, modify to market execution
        client.modify_order(
            app_order_id=app_order_id,
            order_type=client.interactive.ORDER_TYPE_MARKET,
            stop_price=0,      # Market order
            limit_price=0      # Market order
        )
        
    elif status == "FILLED":
        # Already closed by individual SL hit, skip
        logger.info(f"ℹ️  Strategy {strategy['name']} SL order {app_order_id} already FILLED")
        
    elif status in ("REJECTED", "CANCELLED"):
        # SL order failed, position exposed, warn
        logger.warning(f"⚠️  Strategy {strategy['name']} SL order {app_order_id} {status}")
```

---

## Partial Closure Scenario

**Real-world example:** Short straddle gets hit by market movement, one leg SL fills, other doesn't.

```
Initial: S0921 shorts -260 qty CE @ 19900, -260 qty PE @ 19900
         CE SL @20000, PE SL @19800

T=30s:  Market spikes up 100 points
        → CE SL HITS, order FILLED
        → PE SL still NEW (market went up, down-side premium down)

T=60s:  Market reverses sharply down
        → Strategy SL breaches (big loss)
        → Bot detects: MTM < -16000 threshold

Closure Logic:
1. Check CE SL order → Status = "FILLED" → Skip (already closed)
2. Check PE SL order → Status = "NEW" → Modify to market execution
3. PE market order executes
4. Strategy now closed (CE via individual SL, PE via market order)

Result: ✅ Only open legs closed, no double-close
```

---

## Error Handling

### Order Book Fetch Failure
```python
try:
    order_book = client.get_order_book()
except Exception as e:
    logger.error(f"Failed to fetch order book: {e}")
    return  # Don't crash, just skip closure attempt
```

### Order Not in Book
```python
order_detail = order_book.get(str(app_order_id), {})
if not order_detail:
    logger.warning(f"SL order {app_order_id} not found in order book")
    continue  # Skip and move to next
```

### Modify Order Failure
```python
try:
    client.modify_order(...)
except Exception as e:
    logger.error(f"Failed to modify order {app_order_id}: {e}")
    # Don't crash, position will try to close on next cycle
```

### DEMO_MODE Handling
```python
def _close_strategy_via_open_sl_orders(client: XTSClient, strategy: dict) -> None:
    if client is None:  # DEMO_MODE
        logger.info(f"[DEMO] Would close {strategy['name']}")
        return  # No actual operations in demo
```

---

## Test Coverage

### Unit Tests: 10 Tests

**TestCloseStrategyViaOpenSLOrders:**
- ✅ Skips if no SL orders
- ✅ Handles order book fetch errors gracefully
- ✅ Modifies both SL orders if both NEW
- ✅ **Partial closure:** CE FILLED, PE NEW → modifies only PE
- ✅ Handles REPLACED status (reopened orders)
- ✅ Warns on REJECTED SL, skips
- ✅ Warns on CANCELLED SL, skips
- ✅ Warns on missing SL orders in book, continues
- ✅ Logs error on modify_order failure, doesn't crash
- ✅ Multi-strategy isolation: S0921's SL closed independently from S0955

### Integration Tests: 4 Scenarios

**Feature: Multi-strategy conflict fix**
- Scenario 1: Single strategy closure
- Scenario 2: Two strategies on same strikes, close one
- Scenario 3: Partial closure (one leg filled)
- Scenario 4: Error conditions and recovery

---

## Integration with MTM Loop

**Monitoring cycle (every 3 seconds):**

```python
def _monitor_mtm(client, index_config, strategies):
    for strategy in strategies:
        # 1. First, sync SL order status from order book
        _sync_sl_order_status_and_capture_exits(client, strategy)
        
        # 2. Then, check if all positions are closed
        if _check_all_positions_closed(strategy):
            strategy['state'] = 'CLOSED'
            continue
        
        # 3. Calculate MTM with updated exit prices
        mtm = calculate_mtm(strategy)
        
        # 4. Check strategy SL threshold
        if mtm < strategy['strategy_sl']:
            logger.info(f"Strategy SL hit for {strategy['name']}")
            _close_strategy_via_open_sl_orders(client, strategy)  # <- Called here
            strategy['state'] = 'CLOSING'
```

---

## Key Design Decisions

1. **Check Order Book First** - Don't assume positions, verify actual order status
2. **Skip FILLED Orders** - Never try to close already-closed legs
3. **Modify Not Cancel+Place** - More efficient than canceling and placing new orders
4. **Per-Strategy Tags** - Ensures unique identification and isolation
5. **Graceful Degradation** - Log warnings but don't crash on API errors

---

## Configuration

Edit strategy SL thresholds in [config.py](../../config.py):

```python
STRATEGIES = [
    StrategyConfig("S0920", "09:20:00", 8, 20.0, 16000.0),  # SL at -16k
    StrategyConfig("S1001", "10:01:00", 8, 20.0, 16000.0),  # SL at -16k
    StrategyConfig("S1240", "12:40:00", 8, 35.0, 16000.0),  # SL at -16k
    StrategyConfig("S1350", "13:50:00", 8, 35.0, 16000.0),  # SL at -16k
]
```

Where:
- First arg: Strategy name
- Second arg: Execution time
- Third arg: Lot size
- Fourth arg: Per-leg SL percentage
- Fifth arg: Strategy SL threshold (MTM)

---

## Troubleshooting

### Strategy not closing in real trading
1. Check `bot.log` for errors
2. Verify SL order tags are unique per strategy
3. Confirm order book is returning correct status
4. Run `tests/test_expiry_debug.py` to test API connectivity

### Partial closure not working
1. Verify order book shows FILLED status for individual SL fills
2. Check that remaining leg has NEW or REPLACED status
3. Confirm modify_order API is available (requires XTS upgrade)

### Double-close errors
1. This should NOT happen with new implementation
2. If it does: Verify order book fetch is working
3. Check SL order tags are properly set during entry

---

## See Also

- [ORDER_BOOK_MONITORING.md](ORDER_BOOK_MONITORING.md) - How we monitor SL status
- [../ARCHITECTURE.md](../ARCHITECTURE.md) - Overall system design
- [../DEPLOYMENT.md](../DEPLOYMENT.md) - Production setup
