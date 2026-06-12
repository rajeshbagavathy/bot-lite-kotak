from dataclasses import dataclass
import os
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

import boto3


@dataclass(frozen=True)
class IndexConfig:
    name: str
    fno_symbol: str
    spot_exchange_segment: int
    spot_instrument_id: int
    strike_diff: int
    lot_size: int
    option_ltp_segment: int
    option_exchange_segment: str
    order_exchange_segment: str
    tick_size: float = 0.05  # Minimum price increment (required by XTS API)


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    time: str
    lots: int
    leg_sl_pct: float
    strategy_sl: float


NIFTY = IndexConfig(
    name="NIFTY",
    fno_symbol="NIFTY",
    spot_exchange_segment=1,
    spot_instrument_id=26000,
    strike_diff=50,
    lot_size=65,
    option_ltp_segment=2,
    option_exchange_segment="OPTIDX",
    order_exchange_segment="NSEFO",
    tick_size=0.05,
)

SENSEX = IndexConfig(
    name="SENSEX",
    fno_symbol="SENSEX",
    spot_exchange_segment=11,
    spot_instrument_id=26065,
    strike_diff=100,
    lot_size=20,
    option_ltp_segment=12,
    option_exchange_segment="IO",
    order_exchange_segment="BSEFO",
    tick_size=0.05,
)

INDEX_CONFIGS = {
    "NIFTY": NIFTY,
    "SENSEX": SENSEX,
}



STRATEGIES = []  # Deprecated: use day-based plans below.


# === Day-based strategy plans ===
#
# Naming convention:
# - Prefix: N_ (NIFTY), X_ (SENSEX)
# - Day letter: M (Mon), T (Tue), W (Wed), H (Thu), F (Fri)
# - Slot id: S3, S10, S22, etc.
# - Time suffix: 1001 = 10:01, 1146 = 11:46, 1446 = 14:46, etc.
#
# Example: N_M_S10_1146 → NIFTY, Monday, slot S10 at 11:46.
#
# Slots/times match xts-bot-lite. Default 3 lots (STRATEGY_LOTS); Wednesday uses STRATEGY_LOTS_WEDNESDAY (default 1).
STRATEGY_LOTS = int(os.getenv("STRATEGY_LOTS", "3"))
STRATEGY_LOTS_WEDNESDAY = int(os.getenv("STRATEGY_LOTS_WEDNESDAY", "1"))

# Monday NIFTY (sorted by time)
# S2_9.31am, S4_10.01am, S9_11.16am, S11_11.46am, S13_12.16pm, S16_1.01pm, S18_1.31pm, S21_2.16pm
MONDAY_NIFTY_STRATEGIES = [
    StrategyConfig("N_M_0931", "09:31:00", STRATEGY_LOTS, 20.0, 0.0),   # S2_9.31am
    StrategyConfig("N_M_1001", "10:01:00", STRATEGY_LOTS, 20.0, 0.0),  # S4_10.01am
    StrategyConfig("N_M_1116", "11:16:00", STRATEGY_LOTS, 20.0, 0.0),   # S9_11.16am
    StrategyConfig("N_M_1146", "11:46:00", STRATEGY_LOTS, 20.0, 0.0),  # S11_11.46am
    StrategyConfig("N_M_1216", "12:16:00", STRATEGY_LOTS, 20.0, 0.0),   # S13_12.16pm
    StrategyConfig("N_M_1301", "13:01:00", STRATEGY_LOTS, 20.0, 0.0),  # S16_1.01pm
    StrategyConfig("N_M_1331", "13:31:00", STRATEGY_LOTS, 20.0, 0.0),   # S18_1.31pm
    StrategyConfig("N_M_1416", "14:16:00", STRATEGY_LOTS, 20.0, 0.0),   # S21_2.16pm
]

# Monday SENSEX (sorted by time)
MONDAY_SENSEX_STRATEGIES = [
    StrategyConfig("X_M_1001", "10:01:00", STRATEGY_LOTS, 20.0, 0.0),
    StrategyConfig("X_M_1016", "10:16:00", STRATEGY_LOTS, 20.0, 0.0),
    StrategyConfig("X_M_1146", "11:46:00", STRATEGY_LOTS, 20.0, 0.0),
    StrategyConfig("X_M_1446", "14:46:00", STRATEGY_LOTS, 20.0, 0.0),
]

# Tuesday NIFTY (sorted by time)
# S2_9.31am, S6_10.31am, S8_11.01am, S10_11.31am, S12_12.01pm, S14_12.31pm, S17_1.16pm, S21_2.16pm
TUESDAY_NIFTY_STRATEGIES = [
    StrategyConfig("N_T_0931", "09:31:00", STRATEGY_LOTS, 20.0, 0.0),   # S2_9.31am
    StrategyConfig("N_T_1031", "10:31:00", STRATEGY_LOTS, 20.0, 0.0),   # S6_10.31am
    StrategyConfig("N_T_1101", "11:01:00", STRATEGY_LOTS, 20.0, 0.0),   # S8_11.01am
    StrategyConfig("N_T_1131", "11:31:00", STRATEGY_LOTS, 20.0, 0.0),  # S10_11.31am
    StrategyConfig("N_T_1201", "12:01:00", STRATEGY_LOTS, 20.0, 0.0),   # S12_12.01pm
    StrategyConfig("N_T_1231", "12:31:00", STRATEGY_LOTS, 20.0, 0.0),  # S14_12.31pm
    StrategyConfig("N_T_1316", "13:16:00", STRATEGY_LOTS, 20.0, 0.0),  # S17_1.16pm
    StrategyConfig("N_T_1416", "14:16:00", STRATEGY_LOTS, 20.0, 0.0),   # S21_2.16pm
]

# Wednesday – same slots/times as xts-bot-lite; 1 lot per slot (STRATEGY_LOTS_WEDNESDAY).
# Bot picks one index by nearest expiry (SENSEX wins ties), so both lists must exist or the portal shows no rows.
# S1_9.21am, S3_9.46am, S7_10.46am, S9_11.16am, S12_12.01pm, S16_1.01pm, S19_1.46pm, S21_2.16pm
WEDNESDAY_NIFTY_STRATEGIES = [
    StrategyConfig("N_W_0921", "09:21:00", STRATEGY_LOTS_WEDNESDAY, 20.0, 0.0),   # S1_9.21am
    StrategyConfig("N_W_0946", "09:46:00", STRATEGY_LOTS_WEDNESDAY, 20.0, 0.0),   # S3_9.46am
    StrategyConfig("N_W_1046", "10:46:00", STRATEGY_LOTS_WEDNESDAY, 20.0, 0.0),   # S7_10.46am
    StrategyConfig("N_W_1116", "11:16:00", STRATEGY_LOTS_WEDNESDAY, 20.0, 0.0),   # S9_11.16am
    StrategyConfig("N_W_1201", "12:01:00", STRATEGY_LOTS_WEDNESDAY, 20.0, 0.0),   # S12_12.01pm
    StrategyConfig("N_W_1301", "13:01:00", STRATEGY_LOTS_WEDNESDAY, 20.0, 0.0),   # S16_1.01pm
    StrategyConfig("N_W_1346", "13:46:00", STRATEGY_LOTS_WEDNESDAY, 20.0, 0.0),   # S19_1.46pm
    StrategyConfig("N_W_1416", "14:16:00", STRATEGY_LOTS_WEDNESDAY, 20.0, 0.0),   # S21_2.16pm
]
# WEDNESDAY_SENSEX_STRATEGIES = [
#     StrategyConfig("X_W_0921", "09:21:00", STRATEGY_LOTS, 20.0, 0.0),   # S1_9.21am
#     StrategyConfig("X_W_0946", "09:46:00", STRATEGY_LOTS, 20.0, 0.0),   # S3_9.46am
#     StrategyConfig("X_W_1046", "10:46:00", STRATEGY_LOTS, 20.0, 0.0),   # S7_10.46am
#     StrategyConfig("X_W_1116", "11:16:00", STRATEGY_LOTS, 20.0, 0.0),   # S9_11.16am
#     StrategyConfig("X_W_1201", "12:01:00", STRATEGY_LOTS, 20.0, 0.0),   # S12_12.01pm
#     StrategyConfig("X_W_1301", "13:01:00", STRATEGY_LOTS, 20.0, 0.0),   # S16_1.01pm
#     StrategyConfig("X_W_1346", "13:46:00", STRATEGY_LOTS, 20.0, 0.0),   # S19_1.46pm
#     StrategyConfig("X_W_1416", "14:16:00", STRATEGY_LOTS, 20.0, 0.0),   # S21_2.16pm
# ]
WEDNESDAY_SENSEX_STRATEGIES = [
    StrategyConfig("X_W_0921", "09:21:00", STRATEGY_LOTS_WEDNESDAY, 20.0, 0.0),   # S1_9.21am
    StrategyConfig("X_W_0946", "09:46:00", STRATEGY_LOTS_WEDNESDAY, 20.0, 0.0),   # S3_9.46am
    StrategyConfig("X_W_1046", "10:46:00", STRATEGY_LOTS_WEDNESDAY, 20.0, 0.0),   # S7_10.46am
    StrategyConfig("X_W_1116", "11:16:00", STRATEGY_LOTS_WEDNESDAY, 20.0, 0.0),   # S9_11.16am
    StrategyConfig("X_W_1201", "12:01:00", STRATEGY_LOTS_WEDNESDAY, 20.0, 0.0),   # S12_12.01pm
    StrategyConfig("X_W_1301", "13:04:00", STRATEGY_LOTS_WEDNESDAY, 20.0, 0.0),   # S16_1.01pm
    StrategyConfig("X_W_1346", "13:46:00", STRATEGY_LOTS_WEDNESDAY, 20.0, 0.0),   # S19_1.46pm
    StrategyConfig("X_W_1416", "14:16:00", STRATEGY_LOTS_WEDNESDAY, 20.0, 0.0),   # S21_2.16pm
]
# Thursday SENSEX (sorted by time)
# Slot / score / note from expectancy backtest (see table in repo history).
THURSDAY_SENSEX_STRATEGIES = [
    StrategyConfig("X_H_0946", "09:46:00", STRATEGY_LOTS, 20.0, 0.0),  # S3_9.46am  score 2.876  High expectancy, solid Return/MDD, stable streaks
    StrategyConfig("X_H_1016", "10:16:00", STRATEGY_LOTS, 20.0, 0.0),  # S5_10.16am score 2.436  High expectancy, solid Return/MDD, stable streaks
    StrategyConfig("X_H_1101", "11:01:00", STRATEGY_LOTS, 20.0, 0.0),   # S8_11.01am score -0.081 High expectancy, solid Return/MDD
    StrategyConfig("X_H_1131", "11:31:00", STRATEGY_LOTS, 20.0, 0.0),  # S10_11.31am score -3.693 Diversification / score
    StrategyConfig("X_H_1231", "12:31:00", STRATEGY_LOTS, 20.0, 0.0),  # S14_12.31pm score -1.267 Diversification / score
    StrategyConfig("X_H_1301", "13:01:00", STRATEGY_LOTS, 20.0, 0.0),  # S16_1.01pm  score 0.317  High expectancy, stable streaks
    StrategyConfig("X_H_1331", "13:31:00", STRATEGY_LOTS, 20.0, 0.0),  # S18_1.31pm  score -2.149 Diversification / score
    StrategyConfig("X_H_1401", "14:01:00", STRATEGY_LOTS, 20.0, 0.0),  # S20_2.01pm  score 1.560  solid Return/MDD, stable streaks
]

# Friday NIFTY (sorted by time)
FRIDAY_NIFTY_STRATEGIES = [
    StrategyConfig("N_F_0931", "09:31:00", STRATEGY_LOTS, 20.0, 0.0),  # S2_9.31am  score 2.912  High expectancy, solid Return/MDD, stable streaks
    StrategyConfig("N_F_1031", "10:31:00", STRATEGY_LOTS, 20.0, 0.0),  # S6_10.31am score 1.318  High expectancy, solid Return/MDD, stable streaks
    StrategyConfig("N_F_1101", "11:01:00", STRATEGY_LOTS, 20.0, 0.0),  # S8_11.01am score 1.710  High expectancy, solid Return/MDD, stable streaks
    StrategyConfig("N_F_1131", "11:31:00", STRATEGY_LOTS, 20.0, 0.0),   # S10_11.31am score -2.037 High expectancy
    StrategyConfig("N_F_1231", "12:31:00", STRATEGY_LOTS, 20.0, 0.0),   # S14_12.31pm score -0.951 solid Return/MDD, stable streaks
    StrategyConfig("N_F_1301", "13:01:00", STRATEGY_LOTS, 20.0, 0.0),   # S16_1.01pm  score -2.403 Diversification / score
    StrategyConfig("N_F_1401", "14:01:00", STRATEGY_LOTS, 20.0, 0.0),   # S20_2.01pm  score -0.548 stable streaks
    StrategyConfig("N_F_1431", "14:31:00", STRATEGY_LOTS, 20.0, 0.0),   # S22_2.31pm  score -0.000 stable streaks
]


def get_today_strategies(index_name: str) -> list[StrategyConfig]:
    """Return today's strategies for the given index (NIFTY/SENSEX). Uses IST weekday."""
    import datetime

    import pytz

    weekday = datetime.datetime.now(pytz.timezone("Asia/Kolkata")).weekday()  # 0=Mon … 6=Sun

    if weekday == 0:  # Monday
        if index_name.upper() == "NIFTY":
            return MONDAY_NIFTY_STRATEGIES
        if index_name.upper() == "SENSEX":
            return MONDAY_SENSEX_STRATEGIES
    if weekday == 1:  # Tuesday
        if index_name.upper() == "NIFTY":
            return TUESDAY_NIFTY_STRATEGIES
    if weekday == 2:  # Wednesday
        if index_name.upper() == "NIFTY":
            return WEDNESDAY_NIFTY_STRATEGIES
        if index_name.upper() == "SENSEX":
            return WEDNESDAY_SENSEX_STRATEGIES
    if weekday == 3:  # Thursday
        if index_name.upper() == "SENSEX":
            return THURSDAY_SENSEX_STRATEGIES
    if weekday == 4:  # Friday
        if index_name.upper() == "NIFTY":
            return FRIDAY_NIFTY_STRATEGIES

    return []

PORTFOLIO_SL_LIMIT = -80000.0

# EOD: square off bot-tracked F&O positions and cancel bot SL orders (IST wall clock).
EOD_SQUAREOFF_ENABLED = os.getenv("EOD_SQUAREOFF_ENABLED", "True").lower() in ("true", "1", "yes")
EOD_SQUAREOFF_TIME = os.getenv("EOD_SQUAREOFF_TIME", "15:10")
# Hard stop for EOD retry loop (IST). Retries run from EOD_SQUAREOFF_TIME until this time (exclusive).
EOD_VERIFY_UNTIL = os.getenv("EOD_VERIFY_UNTIL", "15:19")
EOD_VERIFY_INTERVAL_SEC = int(os.getenv("EOD_VERIFY_INTERVAL_SEC", "15"))
# Re-place EOD close on same signed qty only after this many seconds (broker position lag).
EOD_CLOSE_STALE_RETRY_SEC = int(os.getenv("EOD_CLOSE_STALE_RETRY_SEC", "45"))
# Set to True to enable trading on non-expiry days; False disables all strategies on non-expiry.
TRADE_NON_EXPIRY_DAY = os.getenv("TRADE_NON_EXPIRY_DAY", "True").lower() in ("true", "1", "yes")

# Margin + hedging configuration
# User baseline: one lot ATM short straddle + hedges ~= ₹1.5L.
MARGIN_REQUIRED_PER_LOT_EXPIRY = float(os.getenv("MARGIN_REQUIRED_PER_LOT_EXPIRY", "150000"))
MARGIN_REQUIRED_PER_LOT_EXPIRY_SENSEX = float(os.getenv("MARGIN_REQUIRED_PER_LOT_EXPIRY_SENSEX", "150000"))
MARGIN_REQUIRED_PER_LOT_NON_EXPIRY = float(os.getenv("MARGIN_REQUIRED_PER_LOT_NON_EXPIRY", "150000"))
MARGIN_BUFFER_EXPIRY = float(os.getenv("MARGIN_BUFFER_EXPIRY", "0"))
MARGIN_BUFFER_NON_EXPIRY = float(os.getenv("MARGIN_BUFFER_NON_EXPIRY", "0"))
MARGIN_TIGHT_BUFFER_MIN = float(os.getenv("MARGIN_TIGHT_BUFFER_MIN", "200000"))
# If margin is at least this (and >= 1-lot requirement), place at least 1 lot instead of skipping.
MIN_MARGIN_TO_TRADE = float(os.getenv("MIN_MARGIN_TO_TRADE", "2000000"))


def margin_required_per_lot_expiry(index_name: Optional[str]) -> float:
    """Margin per straddle lot (CE+PE) on expiry day; NIFTY vs SENSEX."""
    name = str(index_name or "").strip().upper()
    if name == "SENSEX":
        return float(MARGIN_REQUIRED_PER_LOT_EXPIRY_SENSEX)
    return float(MARGIN_REQUIRED_PER_LOT_EXPIRY)


# Hedge quantity multipliers (legacy; incremental hedges use open-short + planned entry qty).
HEDGE_QTY_MULTIPLIER_EXPIRY = float(os.getenv("HEDGE_QTY_MULTIPLIER_EXPIRY", "2.0"))
HEDGE_QTY_MULTIPLIER_NON_EXPIRY = float(os.getenv("HEDGE_QTY_MULTIPLIER_NON_EXPIRY", "1.5"))
# Before each straddle, buy far-OTM PE+CE hedges for this entry (incremental vs existing hedges).
HEDGE_ON_EVERY_STRATEGY = os.getenv("HEDGE_ON_EVERY_STRATEGY", "True").lower() in ("true", "1", "yes")
# ITM strikes away from ATM for expiry days (NIFTY: 2 → 25100 CE / 25300 PE @ spot 25200; SENSEX: 3). Non-expiry uses ATM.
ITM_STRIKES_NIFTY = int(os.getenv("ITM_STRIKES_NIFTY", "1"))
ITM_STRIKES_SENSEX = int(os.getenv("ITM_STRIKES_SENSEX", "2"))
# Leg SL % on non-expiry day (override per-strategy); expiry uses strategy leg_sl_pct.
LEG_SL_PCT_NON_EXPIRY = float(os.getenv("LEG_SL_PCT_NON_EXPIRY", "20.0"))
# Leg target %: if a leg's profit (as % of executed sell order premium / entry_price) reaches this, close that leg by modifying SL to market.
# Target is calculated on executed sell order premium: profit_pct = (entry_price - ltp) / entry_price * 100.
LEG_TARGET_PCT_EXPIRY = float(os.getenv("LEG_TARGET_PCT_EXPIRY", "80.0"))
LEG_TARGET_PCT_NON_EXPIRY = float(os.getenv("LEG_TARGET_PCT_NON_EXPIRY", "50.0"))

# Fractional buffer for market-style LIMIT orders (e.g. 0.01 = 1%). BUY: LTP*(1+s), SELL: LTP*(1-s).
MARKETABLE_LIMIT_SLIPPAGE_PCT = float(os.getenv("MARKETABLE_LIMIT_SLIPPAGE_PCT", "0.01"))

# Premium-based straddle strike selection (optional). If set, CE/PE strikes are chosen by option LTP, not ATM.
# NIFTY: target 100, buffer 15 → allowed range 85–115. SENSEX: target 300, buffer 40 → 260–340.
# Within range, the strike with premium closest to target is picked. If no strike in range, strategy is skipped.
USE_PREMIUM_BASED_STRIKE = os.getenv("USE_PREMIUM_BASED_STRIKE", "True").lower() in ("true", "1", "yes")
STRIKE_PREMIUM_TARGET_NIFTY = float(os.getenv("STRIKE_PREMIUM_TARGET_NIFTY", "100"))
STRIKE_PREMIUM_BUFFER_NIFTY = float(os.getenv("STRIKE_PREMIUM_BUFFER_NIFTY", "15"))
STRIKE_PREMIUM_TARGET_SENSEX = float(os.getenv("STRIKE_PREMIUM_TARGET_SENSEX", "300"))
STRIKE_PREMIUM_BUFFER_SENSEX = float(os.getenv("STRIKE_PREMIUM_BUFFER_SENSEX", "40"))

# Strategy-level SL: if False or strategy_sl <= 0, per-strategy stop-loss is not applied (only portfolio SL and leg SL apply).
STRATEGY_SL_ENABLED = os.getenv("STRATEGY_SL_ENABLED", "False").lower() in ("true", "1", "yes")

# After one leg's SL fills, tighten the surviving leg's SL to original short price (entry/cost).
SURVIVOR_SL_TO_COST_ENABLED = os.getenv("SURVIVOR_SL_TO_COST_ENABLED", "True").lower() in ("true", "1", "yes")

# Target premium for hedge selection (approx LTP per option).
HEDGE_TARGET_PREMIUM_EXPIRY = float(os.getenv("HEDGE_TARGET_PREMIUM_EXPIRY", "5"))
HEDGE_TARGET_PREMIUM_NON_EXPIRY = float(os.getenv("HEDGE_TARGET_PREMIUM_NON_EXPIRY", "10"))
# Allowed LTP range for hedge strikes (reject if outside). Expiry: 2–8, non-expiry: 7–13.
HEDGE_PREMIUM_MIN_EXPIRY = float(os.getenv("HEDGE_PREMIUM_MIN_EXPIRY", "2"))
HEDGE_PREMIUM_MAX_EXPIRY = float(os.getenv("HEDGE_PREMIUM_MAX_EXPIRY", "8"))
HEDGE_PREMIUM_MIN_NON_EXPIRY = float(os.getenv("HEDGE_PREMIUM_MIN_NON_EXPIRY", "7"))
HEDGE_PREMIUM_MAX_NON_EXPIRY = float(os.getenv("HEDGE_PREMIUM_MAX_NON_EXPIRY", "13"))

SOURCE = "WEBAPI"
DEMO_MODE = os.getenv("DEMO_MODE", "False").lower() in ("true", "1", "yes")  # Reads from environment variable

# AWS SSM Parameter Store (EC2: grant ssm:GetParameter on these paths; region ap-south-1 by default).
SSM_BASE_PATH = os.getenv("SSM_BASE_PATH", "/trade/config").rstrip("/")
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
# Legacy XTS layout: /trade/config/<ACC_NAME>/apikey, etc.
ACC_NAME = os.getenv("ACC_NAME")
# UI basic auth — /trade/config/5pindra/loginusername|loginpassword
SSM_LOGIN_USERNAME_PATH = os.getenv(
    "SSM_LOGIN_USERNAME_PATH",
    f"{SSM_BASE_PATH}/5pindra/loginusername",
)
SSM_LOGIN_PASSWORD_PATH = os.getenv(
    "SSM_LOGIN_PASSWORD_PATH",
    f"{SSM_BASE_PATH}/5pindra/loginpassword",
)
# Kotak — /trade/config/kotak/<profile>/KOTAK_* (see load_kotak_credentials)
KOTAK_SSM_PROFILE = os.getenv("KOTAK_SSM_PROFILE", "rajesh")

# Calm zone / OHLC: optional correction (seconds) added to vendor unix timestamps before IST formatting.
# Default -19800 aligns feeds that are consistently +5h30 ahead of India wall clock.
# Override via env if your vendor/account feed differs.
CALM_ZONE_BAR_UNIX_OFFSET_SEC = int(os.getenv("CALM_ZONE_BAR_UNIX_OFFSET_SEC", "-19800"))
USE_CALM_ZONE_GATEKEEPER = os.getenv("USE_CALM_ZONE_GATEKEEPER", "True").lower() in ("true", "1", "yes")
# current_or_prior_calm (default): pass if newest 1m row OR the immediately prior row is calm.
# latest_bar: only the newest 1m row may pass.
# recent_calm: pass if any calm bar exists within CALM_ZONE_RECENT_CALM_MINUTES (wall clock). Legacy / loose.
CALM_ZONE_GATEKEEPER_MODE = os.getenv("CALM_ZONE_GATEKEEPER_MODE", "current_or_prior_calm").strip().lower()
CALM_ZONE_RECENT_CALM_MINUTES = int(os.getenv("CALM_ZONE_RECENT_CALM_MINUTES", "12"))
CALM_ZONE_WAIT_TIMEOUT_MINUTES = int(os.getenv("CALM_ZONE_WAIT_TIMEOUT_MINUTES", "30"))
CALM_ZONE_POLL_SECONDS = int(os.getenv("CALM_ZONE_POLL_SECONDS", "60"))
# How often WAITING_FOR_CALM strategies re-check (match xts-bot-lite default 15s).
CALM_ZONE_GATEKEEPER_POLL_SECONDS = int(
    os.getenv("CALM_ZONE_GATEKEEPER_POLL_SECONDS", "15")
)
# Stop revising OHLC for bars older than this many seconds (vendor bar_unix vs wall clock). 0 = off.
CALM_ZONE_OHLC_FREEZE_AFTER_SEC = int(os.getenv("CALM_ZONE_OHLC_FREEZE_AFTER_SEC", "300"))
# OHLC pull window for calm zone: unset or empty = from today's cash open (09:15 IST) through now
# (full session backfill each tick). Set to e.g. 25 for a rolling 25-minute window only.
_calm_lb = os.getenv("CALM_ZONE_OHLC_LOOKBACK_MINUTES", "").strip()
CALM_ZONE_OHLC_LOOKBACK_MINUTES: Optional[int] = None if _calm_lb == "" else int(_calm_lb)

# Database configuration
DB_PATH = os.getenv("DB_PATH", "trades.db")
DB_RETENTION_DAYS = int(os.getenv("DB_RETENTION_DAYS", "30"))
DB_ENABLE_MTM_SNAPSHOTS = os.getenv("DB_ENABLE_MTM_SNAPSHOTS", "false").lower() in ("true", "1", "yes")


def _get_ssm_param(param_name: str) -> str:
    client = boto3.client("ssm", region_name=AWS_REGION)
    response = client.get_parameter(Name=param_name, WithDecryption=True)
    return response["Parameter"]["Value"]


def _build_ssm_path(param_name: str) -> str:
    if not ACC_NAME:
        raise RuntimeError("ACC_NAME is required to load XTS SSM parameters")
    return f"{SSM_BASE_PATH}/{ACC_NAME}/{param_name}"


def _kotak_ssm_base() -> str:
    return os.getenv(
        "SSM_KOTAK_BASE_PATH",
        f"{SSM_BASE_PATH}/kotak/{KOTAK_SSM_PROFILE}",
    ).rstrip("/")


def _kotak_ssm_path(param_name: str) -> str:
    """Full path, e.g. /trade/config/kotak/rajesh/KOTAK_CONSUMER_KEY_S."""
    return f"{_kotak_ssm_base()}/{param_name}"


def _get_env_or_ssm_path(env_key: str, ssm_path: str) -> str:
    """Prefer env var; otherwise read the full SSM parameter path."""
    value = os.getenv(env_key)
    if value:
        return value
    return _get_ssm_param(ssm_path)


def _get_env_or_ssm(env_key: str, ssm_key: str) -> str:
    """Legacy XTS: env var or /trade/config/<ACC_NAME>/<ssm_key>."""
    value = os.getenv(env_key)
    if value:
        return value
    return _get_ssm_param(_build_ssm_path(ssm_key))


def _get_env_or_ssm_optional(env_key: str, ssm_path: str) -> Optional[str]:
    value = (os.getenv(env_key) or "").strip()
    if value:
        return value
    try:
        return _get_ssm_param(ssm_path).strip() or None
    except Exception:
        return None


def load_login_credentials() -> dict:
    """Dashboard basic-auth credentials (env or SSM under 5pindra paths)."""
    return {
        "login_username": _get_env_or_ssm_path("LOGIN_USERNAME_5P", SSM_LOGIN_USERNAME_PATH),
        "login_password": _get_env_or_ssm_path("LOGIN_PASSWORD_5P", SSM_LOGIN_PASSWORD_PATH),
    }


def load_credentials() -> dict:
    return {
        "api_key": _get_env_or_ssm("XTS_API_KEY_5P_S", "apikey"),
        "api_secret": _get_env_or_ssm("XTS_API_SECRET_5P_S", "apisecret"),
        "client_id": _get_env_or_ssm("XTS_5P_CLIENTID_5P_S", "clientid"),
        "market_api_key": _get_env_or_ssm("XTS_MARKET_API_KEY_5P_S", "marketdataapikey"),
        "market_api_secret": _get_env_or_ssm("XTS_MARKET_API_SECRET_5P_S", "marketdataapisecret"),
        **load_login_credentials(),
    }


def get_basic_auth_creds(creds: dict) -> dict:
    return {
        "username": os.getenv("BASIC_AUTH_USERNAME", creds.get("login_username")),
        "password": os.getenv("BASIC_AUTH_PASSWORD", creds.get("login_password")),
    }


# --- Kotak Neo (optional; used when BROKER_BACKEND=kotak) ---
BROKER_BACKEND = os.getenv("BROKER_BACKEND", "xts").strip().lower()


def load_kotak_credentials() -> dict:
    """Credentials for Kotak Neo API (TOTP + MPIN flow). Env overrides SSM paths under /trade/config/kotak/<profile>/."""
    return {
        "consumer_key": _get_env_or_ssm_path(
            "KOTAK_CONSUMER_KEY_S", _kotak_ssm_path("KOTAK_CONSUMER_KEY_S")
        ),
        "mobile_number": _get_env_or_ssm_path(
            "KOTAK_MOBILE_S", _kotak_ssm_path("KOTAK_MOBILE_S")
        ),
        "ucc": _get_env_or_ssm_path("KOTAK_UCC_S", _kotak_ssm_path("KOTAK_UCC_S")),
        "mpin": _get_env_or_ssm_path("KOTAK_MPIN_S", _kotak_ssm_path("KOTAK_MPIN_S")),
        "totp_secret": _get_env_or_ssm_optional(
            "KOTAK_TOTP_SECRET", _kotak_ssm_path("KOTAK_TOTP_SECRET")
        ),
        "environment": os.getenv("KOTAK_ENVIRONMENT", "prod").strip().lower(),
        "neo_fin_key": os.getenv("KOTAK_NEO_FIN_KEY") or None,
    }
