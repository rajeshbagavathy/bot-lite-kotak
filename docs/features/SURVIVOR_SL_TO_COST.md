# Survivor leg SL tightened to cost

When one leg of a short straddle is stopped out because its **stop-limit SL order fills** (`closed_via: SL_FILLED`), the bot tightens the **remaining** leg’s pending SL to the **original short price** (entry / collected premium), using the same tick rounding as initial SL placement.

## When it runs

- Strategy status `OPEN`, exactly **two** positions, **one** closed with `closed_via == "SL_FILLED"` and **one** still open.
- The survivor’s SL order is still open in the order book (`NEW`, `REPLACED`, `PENDING`, `OPEN`, or partial fills).
- Feature flag: `SURVIVOR_SL_TO_COST_ENABLED` (default `True`) in `config.py` / env `SURVIVOR_SL_TO_COST_ENABLED`.

## When it does **not** run

- First leg closed via **target** (modify SL to market), **broker sync**, or anything other than `SL_FILLED`.
- Strategy already has `survivor_sl_adjusted_to_cost: true` (idempotent).
- Feature disabled.

## Implementation

- `bot._adjust_survivor_sl_to_cost_after_peer_sl` — called in `_monitor_mtm` immediately after `_sync_sl_order_status_and_capture_exits`, before `_check_leg_target_and_close`.
