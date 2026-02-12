"""SQLite database layer for trading data persistence."""
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import pytz

logger = logging.getLogger("xts-bot-lite")

DB_PATH = "trades.db"
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
        
        conn.commit()
        conn.close()
        logger.info(f"Database initialized at {DB_PATH}")
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
            (strategy_name, order_tag, instrument_id, symbol, quantity, order_type, order_side, status, traded_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (strategy_name, order_tag, instrument_id, symbol, quantity, order_type, order_side, "Pending", traded_price))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to log order: {e}")


def update_order_status(order_tag: str, status: str, traded_price: Optional[float] = None) -> None:
    """Update order status when filled."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        if traded_price is not None:
            cursor.execute("""
                UPDATE orders SET status = ?, traded_price = ? WHERE order_tag = ?
            """, (status, traded_price, order_tag))
        else:
            cursor.execute("""
                UPDATE orders SET status = ? WHERE order_tag = ?
            """, (status, order_tag))
        
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
        logger.info(f"Logged closed trade: {strategy_name} P&L={realized_pnl:.2f}")
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
        logger.info(f"Cleanup: Deleted {deleted_count} old trades (before {cutoff_date})")
    except Exception as e:
        logger.error(f"Failed to cleanup old data: {e}")


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
            positions.append({
                "symbol": row["symbol"],
                "instrument_id": row["instrument_id"],
                "quantity": row["quantity"],
                "entry_price": row["entry_price"],
                "exit_price": row["exit_price"],
            })
        
        return positions
    except Exception as e:
        logger.error(f"Failed to restore positions for strategy {strategy_id}: {e}")
        return []


def restore_todays_strategies() -> List[Dict[str, Any]]:
    """Restore ONLY today's OPEN strategies from database.
    
    Returns list of strategy data with positions for strategies that:
    - Were executed TODAY (execution_date = today in IST)
    - Are currently OPEN (not CLOSED/CLOSING)
    
    STRICT DATE FILTER: Only today's data is restored.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        today = get_ist_date()
        
        # STRICT: Only today's open strategies
        cursor.execute("""
            SELECT * FROM strategies
            WHERE execution_date = ? AND status = 'OPEN'
            ORDER BY id
        """, (today,))
        
        rows = cursor.fetchall()
        conn.close()
        
        restored_strategies = []
        for row in rows:
            strategy_data = {
                "db_id": row["id"],
                "strategy_name": row["strategy_name"],
                "strike": row["strike"],
                "entry_time": row["entry_time"],
                "lots": row["lots"],
                "leg_sl_pct": row["leg_sl_pct"],
                "strategy_sl": row["strategy_sl"],
                "positions": restore_positions_for_strategy(row["id"]),
            }
            restored_strategies.append(strategy_data)
            logger.info(f"Restored strategy from DB: {row['strategy_name']} (strike={row['strike']}, {len(strategy_data['positions'])} positions)")
        
        if restored_strategies:
            logger.info(f"✅ Restored {len(restored_strategies)} strategies from TODAY ({today})")
        else:
            logger.info(f"No strategies to restore from today ({today})")
        
        return restored_strategies
    except Exception as e:
        logger.error(f"Failed to restore today's strategies: {e}")
        return []
