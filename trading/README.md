# Trading package (modular bot engine)

Mirrors the layout from `xts-bot-lite` with Kotak-specific wiring.

## Layout

| Module | Responsibility |
|--------|----------------|
| `journal.py` | Per-strategy JSONL audit trail (`trade_journal.jsonl`) |
| `strategy/gatekeeper.py` | Calm zone checks, wait/retry loop |
| `strategy/margin.py` | Margin, hedges, lot sizing |
| `strategy/strikes.py` | Premium / hedge strike selection |
| `strategy/executor.py` | Full entry pipeline → SL protection |
| `orders/lifecycle.py` | Entry fill wait → SL place → verify → PROTECTED |
| `orders/sl.py` | Leg SL placement |
| `orders/close.py` | Flatten / cancel with journal attribution |

## Journal

- File: `trade_journal.jsonl` (next to `bot.log`, or `TRADE_JOURNAL_PATH`)
- Dashboard: **Trade Journal** tab
- CLI: `jq 'select(.strategy=="X_H_1231")' trade_journal.jsonl`

## Migration status

- **Done:** strategy entry path, calm gatekeeper poll, SL lifecycle, journal UI
- **Still in `bot.py`:** MTM monitor, survivor SL-to-cost, scheduler, Kotak bootstrap
