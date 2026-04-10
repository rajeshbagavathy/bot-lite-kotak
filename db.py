"""SQLite database layer for trading data persistence."""
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import pytz

try:
    from config import LEG_TARGET_PCT, DB_PATH
except ImportError:
    LEG_TARGET_PCT = 65.0
    DB_PATH = "trades.db"

logger = logging.getLogger("xts-bot-lite")
RETENTION_DAYS = 30

# IST timezone
IST = pytz.timezone('Asia/Kolkata')


def get_ist_now() -> datetime:
    """Get current time in IST."""
    return datetime.now(IST)


def get_ist_date() -> str:
    """Get current date in IST (YYYY-MM-DD format)."""
    return get_ist_now().strftime("%Y-%m-%d")


def get_ist_timestamp() -> str:
    """Get current timestamp in IST (ISO format)."""
    return get_ist_now().strftime("%Y-%m-%d %H:%M:%S")


def init_db() -> None:
    """Initialize SQLite database and create schema."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Strategies table - stores strategy execution records
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT NOT NULL,
                execution_date TEXT NOT NULL,
                strike INTEGER,
                entry_time TEXT,
                status TEXT,
                lots INTEGER,
                leg_sl_pct REAL,
                strategy_sl REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Positions table - stores entry/exit positions per strategy
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id INTEGER,
                symbol TEXT NOT NULL,
                instrument_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                entry_time TEXT,
                exit_time TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (strategy_id) REFERENCES strategies(id)
            )
        """)
        
        # Orders table - stores all orders (entry/SL/exit)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT NOT NULL,
                app_order_id INTEGER,
                order_tag TEXT UNIQUE,
                instrument_id INTEGER,
                symbol TEXT,
                quantity INTEGER,
                order_type TEXT,
                order_side TEXT,
                status TEXT,
                traded_price REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Backwards-compatible migration: add app_order_id if an older DB exists.
        cursor.execute("PRAGMA table_info(orders)")
        cols = [row[1] for row in cursor.fetchall()]
        if "app_order_id" not in cols:
            cursor.execute("ALTER TABLE orders ADD COLUMN app_order_id INTEGER")
        
        # Trades (closed records) - stores finalized P&L
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades_closed (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT NOT NULL,
                execution_date TEXT NOT NULL,
                strike INTEGER,
                entry_time TEXT,
                exit_time TEXT,
                realized_pnl REAL,
                mtm_final REAL,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # MTM snapshots (optional, for historical analysis)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mtm_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT NOT NULL,
                mtm REAL,
                realized REAL,
                unrealized REAL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Spot index 1m OHLC + calm-zone metrics (NIFTY / SENSEX)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS spot_market_data (
                index_name TEXT NOT NULL,
                bar_time TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL,
                range_5m REAL,
                net_body REAL,
                body_range_ratio REAL,
                is_calmzone INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (index_name, bar_time)
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_spot_market_data_time "
            "ON spot_market_data (index_name, bar_time DESC)"
        )

        cursor.execute("PRAGMA table_info(spot_market_data)")
        spot_cols = [row[1] for row in cursor.fetchall()]
        if "bar_unix" not in spot_cols:
            cursor.execute("ALTER TABLE spot_market_data ADD COLUMN bar_unix INTEGER")

        conn.commit()
        conn.close()
        logger.debug(f"Database initialized at {DB_PATH}")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")


def log_strategy_execution(
    strategy_name: str,
    strike: int,
    entry_time: str,
    lots: int,
    leg_sl_pct: float,
    strategy_sl: float,
) -> int:
    """Log strategy execution start. Returns strategy_id."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        execution_date = get_ist_date()
        
        cursor.execute("""
            INSERT INTO strategies 
            (strategy_name, execution_date, strike, entry_time, status, lots, leg_sl_pct, strategy_sl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (strategy_name, execution_date, strike, entry_time, "OPEN", lots, leg_sl_pct, strategy_sl))
        
        conn.commit()
        strategy_id = cursor.lastrowid
        conn.close()
        return strategy_id
    except Exception as e:
        logger.error(f"Failed to log strategy execution: {e}")
        return -1


def log_position(
    strategy_id: int,
    symbol: str,
    instrument_id: int,
    quantity: int,
    entry_price: float,
    entry_time: str,
) -> None:
    """Log entry position."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO positions 
            (strategy_id, symbol, instrument_id, quantity, entry_price, entry_time)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (strategy_id, symbol, instrument_id, quantity, entry_price, entry_time))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to log position: {e}")


def log_order(
    strategy_name: str,
    app_order_id: Optional[int],
    order_tag: str,
    instrument_id: int,
    symbol: str,
    quantity: int,
    order_type: str,
    order_side: str,
    traded_price: Optional[float] = None,
) -> None:
    """Log order details."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO orders 
            (strategy_name, app_order_id, order_tag, instrument_id, symbol, quantity, order_type, order_side, status, traded_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (strategy_name, app_order_id, order_tag, instrument_id, symbol, quantity, order_type, order_side, "Pending", traded_price))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to log order: {e}")


def update_order_status(
    order_tag: Optional[str] = None,
    app_order_id: Optional[int] = None,
    status: str = "",
    traded_price: Optional[float] = None,
) -> None:
    """Update order status when filled."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        if app_order_id is None and not order_tag:
            raise ValueError("Either app_order_id or order_tag must be provided")

        where = "app_order_id = ?" if app_order_id is not None else "order_tag = ?"
        key = app_order_id if app_order_id is not None else order_tag

        if traded_price is not None:
            cursor.execute(
                f"UPDATE orders SET status = ?, traded_price = ? WHERE {where}",
                (status, traded_price, key),
            )
        else:
            cursor.execute(
                f"UPDATE orders SET status = ? WHERE {where}",
                (status, key),
            )
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to update order status: {e}")


def update_position_exit(
    strategy_id: int,
    instrument_id: int,
    exit_price: float,
    exit_time: str,
) -> None:
    """Update position with exit price when SL fills."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE positions 
            SET exit_price = ?, exit_time = ? 
            WHERE strategy_id = ? AND instrument_id = ?
        """, (exit_price, exit_time, strategy_id, instrument_id))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to update position exit: {e}")


def log_trade_closed(
    strategy_name: str,
    strike: int,
    entry_time: str,
    exit_time: str,
    realized_pnl: float,
    mtm_final: float,
    reason: str,
) -> None:
    """Log closed trade with final P&L."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        execution_date = get_ist_date()
        
        cursor.execute("""
            INSERT INTO trades_closed 
            (strategy_name, execution_date, strike, entry_time, exit_time, realized_pnl, mtm_final, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (strategy_name, execution_date, strike, entry_time, exit_time, realized_pnl, mtm_final, reason))
        
        conn.commit()
        conn.close()
        logger.debug(f"Logged closed trade: {strategy_name} P&L={realized_pnl:.2f}")
    except Exception as e:
        logger.error(f"Failed to log closed trade: {e}")


def log_mtm_snapshot(strategy_name: str, mtm: float, realized: float, unrealized: float) -> None:
    """Log MTM snapshot for historical analysis (optional, can be disabled)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO mtm_snapshots (strategy_name, mtm, realized, unrealized)
            VALUES (?, ?, ?, ?)
        """, (strategy_name, mtm, realized, unrealized))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to log MTM snapshot: {e}")


def cleanup_old_data(days: int = RETENTION_DAYS) -> None:
    """Delete trades older than specified days."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cutoff_date = (get_ist_now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        # Delete old records
        cursor.execute("""
            DELETE FROM trades_closed WHERE execution_date < ?
        """, (cutoff_date,))
        
        deleted_count = cursor.rowcount
        
        cursor.execute("""
            DELETE FROM strategies WHERE execution_date < ? AND status = 'CLOSED'
        """, (cutoff_date,))
        
        conn.commit()
        conn.close()
        logger.debug(f"Cleanup: Deleted {deleted_count} old trades (before {cutoff_date})")
    except Exception as e:
        logger.error(f"Failed to cleanup old data: {e}")


def cleanup_previous_day_data() -> None:
    """
    On app startup, delete all data from PREVIOUS DAYS.
    Keep ONLY today's data (execution_date = today).
    
    This ensures:
    - Each trading day starts fresh with clean database
    - Previous day positions, orders, strategies are removed
    - 1m spot OHLC (spot_market_data) keeps only today's bars (IST) for all indices
    - But day-trader might restart app multiple times, so today's data persists
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        today = get_ist_date()
        
        # Step 1: Delete old strategies (and their positions)
        cursor.execute("""
            SELECT id FROM strategies WHERE execution_date < ?
        """, (today,))
        old_strategy_ids = [row[0] for row in cursor.fetchall()]
        
        # Delete positions for old strategies
        for strategy_id in old_strategy_ids:
            cursor.execute("DELETE FROM positions WHERE strategy_id = ?", (strategy_id,))
        
        # Delete old strategies
        deleted = cursor.execute("""
            DELETE FROM strategies WHERE execution_date < ?
        """, (today,)).rowcount
        
        # Step 2: Delete old orders (created before today)
        # Note: created_at uses CURRENT_TIMESTAMP which is UTC, so we compare with IST date
        ist_today = get_ist_date()
        deleted_orders = cursor.execute("""
            DELETE FROM orders WHERE DATE(created_at) < ?
        """, (ist_today,)).rowcount
        
        # Step 3: Delete old closed trades
        deleted_trades = cursor.execute("""
            DELETE FROM trades_closed WHERE execution_date < ?
        """, (today,)).rowcount
        
        # Step 4: Delete old MTM snapshots
        # Note: mtm_snapshots uses CURRENT_TIMESTAMP, compare with IST date
        deleted_mtm = cursor.execute("""
            DELETE FROM mtm_snapshots WHERE DATE(timestamp) < ?
        """, (ist_today,)).rowcount

        # Step 5: 1m spot OHLC (all indices) — keep only today (IST) so charts are not mixed across sessions.
        deleted_spot = cursor.execute(
            """
            DELETE FROM spot_market_data
            WHERE bar_time IS NOT NULL AND substr(bar_time, 1, 10) < ?
            """,
            (today,),
        ).rowcount
        
        conn.commit()
        conn.close()
        
        logger.debug(f"🧹 Startup Cleanup: Deleted data from previous days (before {today})")
        if deleted > 0 or deleted_orders > 0 or deleted_trades > 0 or deleted_mtm > 0 or deleted_spot > 0:
            logger.debug(
                f"   - Strategies: {deleted} | Orders: {deleted_orders} | Closed Trades: {deleted_trades} "
                f"| MTM: {deleted_mtm} | Spot 1m: {deleted_spot}"
            )
            logger.debug(f"✅ Previous day data cleaned up. Today starts fresh!")
        else:
            logger.debug(f"✅ No previous day data to cleanup. Today ({today}) starts clean.")
            
    except Exception as e:
        logger.error(f"Failed to cleanup previous day data: {e}")


def get_strategy_record(strategy_name: str, execution_date: str) -> Optional[Dict[str, Any]]:
    """Retrieve strategy record by name and date."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM strategies 
            WHERE strategy_name = ? AND execution_date = ?
            ORDER BY created_at DESC LIMIT 1
        """, (strategy_name, execution_date))
        
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Failed to get strategy record: {e}")
        return None


def get_closed_trades(strategy_name: Optional[str] = None, days: int = 7) -> List[Dict[str, Any]]:
    """Retrieve closed trades for reporting."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cutoff_date = (get_ist_now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        if strategy_name:
            cursor.execute("""
                SELECT * FROM trades_closed 
                WHERE strategy_name = ? AND execution_date >= ?
                ORDER BY created_at DESC
            """, (strategy_name, cutoff_date))
        else:
            cursor.execute("""
                SELECT * FROM trades_closed 
                WHERE execution_date >= ?
                ORDER BY created_at DESC
            """, (cutoff_date,))
        
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to get closed trades: {e}")
        return []


def restore_positions_for_strategy(strategy_id: int) -> List[Dict[str, Any]]:
    """Restore positions for a strategy from database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT symbol, instrument_id, quantity, entry_price, exit_price
            FROM positions
            WHERE strategy_id = ?
            ORDER BY id
        """, (strategy_id,))
        
        rows = cursor.fetchall()
        conn.close()
        
        positions = []
        for row in rows:
            entry = float(row["entry_price"]) if row["entry_price"] is not None else 0.0
            target_price = round(entry * (1 - LEG_TARGET_PCT / 100.0), 2) if entry > 0 else None
            pos: Dict[str, Any] = {
                "symbol": row["symbol"],
                "instrument_id": row["instrument_id"],
                "quantity": row["quantity"],
                "entry_price": row["entry_price"],
                "target_price": target_price,
                "exit_price": row["exit_price"],
            }
            # Positions table has no closed_via column; after restart the bot needs a reason to allow survivor SL→cost.
            if row["exit_price"] is not None:
                pos["closed_via"] = "RESTORED"
            positions.append(pos)
        
        return positions
    except Exception as e:
        logger.error(f"Failed to restore positions for strategy {strategy_id}: {e}")
        return []


def restore_sl_orders_for_strategy(strategy_name: str) -> Dict[str, Any]:
    """
    Restore SL linkage for a strategy from orders table.

    Returns:
      {
        "sl_orders": [{"app_order_id": int, "tag": str}, ...],
        "sl_tag_map": {tag: instrument_id, ...}
      }
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT app_order_id, order_tag, instrument_id
            FROM orders
            WHERE strategy_name = ?
              AND (
                order_type = 'SL'
                OR order_tag LIKE '%_SL_%'
              )
            ORDER BY id DESC
            """,
            (strategy_name,),
        )
        rows = cursor.fetchall()
        conn.close()

        # Keep latest record per tag (rows are DESC by id), then re-sort by tag for stability.
        by_tag: Dict[str, sqlite3.Row] = {}
        for row in rows:
            tag = row["order_tag"]
            if not tag:
                continue
            if tag not in by_tag:
                by_tag[str(tag)] = row

        sl_orders: List[Dict[str, Any]] = []
        sl_tag_map: Dict[str, int] = {}
        for tag in sorted(by_tag.keys()):
            row = by_tag[tag]
            iid = row["instrument_id"]
            if iid is not None:
                try:
                    sl_tag_map[tag] = int(iid)
                except (TypeError, ValueError):
                    pass
            app_oid = row["app_order_id"]
            if app_oid is None:
                continue
            try:
                sl_orders.append({"app_order_id": int(app_oid), "tag": tag})
            except (TypeError, ValueError):
                continue

        return {"sl_orders": sl_orders, "sl_tag_map": sl_tag_map}
    except Exception as e:
        logger.error(f"Failed to restore SL orders for strategy {strategy_name}: {e}")
        return {"sl_orders": [], "sl_tag_map": {}}


def restore_todays_strategies() -> List[Dict[str, Any]]:
    """Restore today's strategies (OPEN and CLOSED) from database.
    
    Returns list of strategy data with positions for strategies that:
    - Were executed TODAY (execution_date = today in IST)
    
    STRICT DATE FILTER: Only today's data is restored.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        today = get_ist_date()
        
        # STRICT: Only today's strategies (OPEN/CLOSED)
        cursor.execute("""
            SELECT * FROM strategies
            WHERE execution_date = ? AND status IN ('OPEN', 'CLOSED')
            ORDER BY id
        """, (today,))
        
        rows = cursor.fetchall()
        conn.close()
        
        restored_strategies = []
        for row in rows:
            sl_restore = restore_sl_orders_for_strategy(row["strategy_name"])
            strategy_data = {
                "db_id": row["id"],
                "strategy_name": row["strategy_name"],
                "strike": row["strike"],
                "entry_time": row["entry_time"],
                "status": row["status"],
                "lots": row["lots"],
                "leg_sl_pct": row["leg_sl_pct"],
                "strategy_sl": row["strategy_sl"],
                "positions": restore_positions_for_strategy(row["id"]),
                "sl_orders": sl_restore["sl_orders"],
                "sl_tag_map": sl_restore["sl_tag_map"],
            }
            restored_strategies.append(strategy_data)
            logger.debug(f"Restored strategy from DB: {row['strategy_name']} (strike={row['strike']}, {len(strategy_data['positions'])} positions)")
        
        if restored_strategies:
            logger.debug(f"✅ Restored {len(restored_strategies)} strategies from TODAY ({today})")
        else:
            logger.debug(f"No strategies to restore from today ({today})")
        
        return restored_strategies
    except Exception as e:
        logger.error(f"Failed to restore today's strategies: {e}")
        return []


def upsert_spot_bar(
    index_name: str,
    bar_time: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: Optional[float],
    range_5m: Optional[float],
    net_body: Optional[float],
    body_range_ratio: Optional[float],
    is_calmzone: bool,
    bar_unix: Optional[int] = None,
) -> None:
    """Insert or replace one row in spot_market_data (bar_time = IST wall clock; bar_unix = raw API epoch seconds)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO spot_market_data (
                index_name, bar_time, open, high, low, close, volume,
                range_5m, net_body, body_range_ratio, is_calmzone, updated_at, bar_unix
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(index_name, bar_time) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume,
                range_5m = excluded.range_5m,
                net_body = excluded.net_body,
                body_range_ratio = excluded.body_range_ratio,
                is_calmzone = excluded.is_calmzone,
                updated_at = excluded.updated_at,
                bar_unix = COALESCE(excluded.bar_unix, spot_market_data.bar_unix)
            """,
            (
                index_name,
                bar_time,
                open_,
                high,
                low,
                close,
                volume,
                range_5m,
                net_body,
                body_range_ratio,
                1 if is_calmzone else 0,
                get_ist_timestamp(),
                bar_unix,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Failed to upsert spot bar %s %s: %s", index_name, bar_time, e)


def fetch_spot_market_rows(index_name: str, limit: int = 120, offset: int = 0) -> List[Dict[str, Any]]:
    """Latest ``limit`` bars for index, newest first, with pagination offset."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT index_name, bar_time, bar_unix, open, high, low, close, volume,
                   range_5m, net_body, body_range_ratio, is_calmzone
            FROM spot_market_data
            WHERE index_name = ?
            ORDER BY (bar_unix IS NULL) ASC, bar_unix DESC, bar_time DESC
            LIMIT ? OFFSET ?
            """,
            (index_name, limit, max(0, int(offset))),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.error("fetch_spot_market_rows failed: %s", e)
        return []


def count_spot_market_rows(index_name: str) -> int:
    """Total count of spot rows for an index."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM spot_market_data
            WHERE index_name = ?
            """,
            (index_name,),
        )
        row = cursor.fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception as e:
        logger.error("count_spot_market_rows failed: %s", e)
        return 0


def fetch_spot_bars_asc_for_recompute(index_name: str, limit: int = 500) -> List[Dict[str, Any]]:
    """Oldest first, for sliding-window recompute."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT index_name, bar_time, bar_unix, open, high, low, close, volume,
                   range_5m, net_body, body_range_ratio, is_calmzone
            FROM spot_market_data
            WHERE index_name = ?
            ORDER BY bar_time DESC
            LIMIT ?
            """,
            (index_name, limit),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        rows.reverse()
        return rows
    except Exception as e:
        logger.error("fetch_spot_bars_asc_for_recompute failed: %s", e)
        return []


def fetch_latest_spot_bar_row(index_name: str) -> Optional[Dict[str, Any]]:
    """Newest 1m spot row for index (by bar_unix / bar_time)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT index_name, bar_time, bar_unix, is_calmzone
            FROM spot_market_data
            WHERE index_name = ?
            ORDER BY (bar_unix IS NULL) ASC, bar_unix DESC, bar_time DESC
            LIMIT 1
            """,
            (index_name,),
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error("fetch_latest_spot_bar_row failed: %s", e)
        return None


# Backward-compatible name (historical callers).
fetch_latest_spot_calm_row = fetch_latest_spot_bar_row


def fetch_recent_calm_spot_row(index_name: str, min_bar_unix: int) -> Optional[Dict[str, Any]]:
    """Most recent calm row with bar_unix >= min_bar_unix (epoch seconds, same basis as stored bar_unix)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT index_name, bar_time, bar_unix, is_calmzone
            FROM spot_market_data
            WHERE index_name = ?
              AND is_calmzone = 1
              AND bar_unix IS NOT NULL
              AND bar_unix >= ?
            ORDER BY bar_unix DESC
            LIMIT 1
            """,
            (index_name, int(min_bar_unix)),
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error("fetch_recent_calm_spot_row failed: %s", e)
        return None


def spot_bar_exists(index_name: str, bar_time: str) -> bool:
    """True if a row exists for this index and bar_time (primary key)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM spot_market_data WHERE index_name = ? AND bar_time = ? LIMIT 1",
            (index_name, bar_time),
        )
        ok = cursor.fetchone() is not None
        conn.close()
        return ok
    except Exception as e:
        logger.error("spot_bar_exists failed: %s", e)
        return False
