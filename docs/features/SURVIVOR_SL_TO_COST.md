# Survivor leg SL tightened to cost

When one leg of a short straddle is stopped out because its **stop-limit SL order fills** (`closed_via: SL_FILLED`), the bot tightens the **remaining** leg’s pending SL to the **original short price** (entry / collected premium), using the same tick rounding as initial SL placement.

## When it runs

- Strategy status `OPEN`, exactly **two** positions, **one** closed and **one** still open.
- The closed leg must have `closed_via` in **`SL_FILLED`** (order book detected SL fill) or **`BROKER_SYNC`** (broker showed flat qty before/without order-book FILLED — common live).
- The survivor’s SL order is still open in the order book (`NEW`, `REPLACED`, `PENDING`, `OPEN`, or partial fills).
- Feature flag: `SURVIVOR_SL_TO_COST_ENABLED` (default `True`) in `config.py` / env `SURVIVOR_SL_TO_COST_ENABLED`.
- In `_monitor_mtm`, this step runs **after** `_sync_strategy_positions_from_broker` so same-tick broker closes are visible.

## When it does **not** run

- First leg closed with a reason we do not treat as peer-exit (e.g. ad-hoc `MANUAL` if ever set), or both legs closed.
- Strategy already has `survivor_sl_adjusted_to_cost: true` (idempotent).
- Feature disabled.

## Implementation

- `bot._adjust_survivor_sl_to_cost_after_peer_sl` — called in `_monitor_mtm` immediately after `_sync_sl_order_status_and_capture_exits`, before `_check_leg_target_and_close`.
