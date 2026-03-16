from dataclasses import dataclass
import os
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

# Monday NIFTY – 14 lots total
MONDAY_NIFTY_STRATEGIES = [
    StrategyConfig("N_M_S10_1146", "11:46:00", 3, 20.0, 0.0),
    StrategyConfig("N_M_S15_1301", "13:01:00", 3, 20.0, 0.0),
    StrategyConfig("N_M_S14_1246", "12:46:00", 3, 20.0, 0.0),
    StrategyConfig("N_M_S11_1201", "12:01:00", 2, 20.0, 0.0),
    StrategyConfig("N_M_S3_1001",  "10:01:00", 2, 20.0, 0.0),
    StrategyConfig("N_M_S12_1216", "12:16:00", 1, 20.0, 0.0),
]

# Monday SENSEX – 6 lots total
MONDAY_SENSEX_STRATEGIES = [
    StrategyConfig("X_M_S3_1001",  "10:01:00", 3, 20.0, 0.0),
    StrategyConfig("X_M_S10_1146", "11:46:00", 1, 20.0, 0.0),
    StrategyConfig("X_M_S4_1016",  "10:16:00", 1, 20.0, 0.0),
    StrategyConfig("X_M_S22_1446", "14:46:00", 1, 20.0, 0.0),
]


def get_today_strategies(index_name: str) -> list[StrategyConfig]:
    """Return today's strategies for the given index (NIFTY/SENSEX)."""
    import datetime

    today = datetime.datetime.now().date()
    weekday = today.weekday()  # 0=Mon,1=Tue,...,6=Sun

    if weekday == 0:  # Monday
        if index_name.upper() == "NIFTY":
            return MONDAY_NIFTY_STRATEGIES
        if index_name.upper() == "SENSEX":
            return MONDAY_SENSEX_STRATEGIES

    # TODO: add Tuesday/Wednesday/Thursday/Friday plans as needed.
    return []

PORTFOLIO_SL_LIMIT = -80000.0
# Set to True to enable trading on non-expiry days; False disables all strategies on non-expiry.
TRADE_NON_EXPIRY_DAY = os.getenv("TRADE_NON_EXPIRY_DAY", "True").lower() in ("true", "1", "yes")

# Margin + hedging configuration
# If available margin is below this, bot will buy far-OTM hedges first.
REQUIRED_MARGIN_PER_STRATEGY = float(os.getenv("REQUIRED_MARGIN_PER_STRATEGY", "1750000"))
# Hedge lots to buy on both sides (CE+PE) when margin is low.
HEDGE_LOTS = int(os.getenv("HEDGE_LOTS", "7"))
# Strategy lots: expiry uses STRATEGIES[].lots (7), non-expiry uses this.
STRATEGY_LOTS_NON_EXPIRY = int(os.getenv("STRATEGY_LOTS_NON_EXPIRY", "4"))
# ITM strikes away from ATM for expiry days (NIFTY: 2 → 25100 CE / 25300 PE @ spot 25200; SENSEX: 3). Non-expiry uses ATM.
ITM_STRIKES_NIFTY = int(os.getenv("ITM_STRIKES_NIFTY", "1"))
ITM_STRIKES_SENSEX = int(os.getenv("ITM_STRIKES_SENSEX", "2"))
# Leg SL % on non-expiry day (override per-strategy); expiry uses strategy leg_sl_pct.
LEG_SL_PCT_NON_EXPIRY = float(os.getenv("LEG_SL_PCT_NON_EXPIRY", "20.0"))
# Leg target %: if a leg's profit (as % of executed sell order premium / entry_price) reaches this, close that leg by modifying SL to market.
# Target is calculated on executed sell order premium: profit_pct = (entry_price - ltp) / entry_price * 100.
# Default is 60% (can be overridden via LEG_TARGET_PCT env).
LEG_TARGET_PCT = float(os.getenv("LEG_TARGET_PCT", "60.0"))

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
SSM_BASE_PATH = "/trade/config"
ACC_NAME = os.getenv("ACC_NAME")

# Database configuration
DB_PATH = os.getenv("DB_PATH", "trades.db")
DB_RETENTION_DAYS = int(os.getenv("DB_RETENTION_DAYS", "30"))
DB_ENABLE_MTM_SNAPSHOTS = os.getenv("DB_ENABLE_MTM_SNAPSHOTS", "false").lower() in ("true", "1", "yes")


def _get_ssm_param(param_name: str) -> str:
    client = boto3.client("ssm", region_name="ap-south-1")
    response = client.get_parameter(Name=param_name, WithDecryption=True)
    return response["Parameter"]["Value"]


def _build_ssm_path(param_name: str) -> str:
    if not ACC_NAME:
        raise RuntimeError("ACC_NAME is required to load SSM parameters")
    return f"{SSM_BASE_PATH}/{ACC_NAME}/{param_name}"


def _get_env_or_ssm(env_key: str, ssm_key: str) -> str:
    value = os.getenv(env_key)
    if value:
        return value
    return _get_ssm_param(_build_ssm_path(ssm_key))


def load_credentials() -> dict:
    return {
        "api_key": _get_env_or_ssm("XTS_API_KEY_5P_S", "apikey"),
        "api_secret": _get_env_or_ssm("XTS_API_SECRET_5P_S", "apisecret"),
        "client_id": _get_env_or_ssm("XTS_5P_CLIENTID_5P_S", "clientid"),
        "market_api_key": _get_env_or_ssm("XTS_MARKET_API_KEY_5P_S", "marketdataapikey"),
        "market_api_secret": _get_env_or_ssm("XTS_MARKET_API_SECRET_5P_S", "marketdataapisecret"),
        "login_username": _get_env_or_ssm("LOGIN_USERNAME_5P", "loginusername"),
        "login_password": _get_env_or_ssm("LOGIN_PASSWORD_5P", "loginpassword"),
    }


def get_basic_auth_creds(creds: dict) -> dict:
    return {
        "username": os.getenv("BASIC_AUTH_USERNAME", creds.get("login_username")),
        "password": os.getenv("BASIC_AUTH_PASSWORD", creds.get("login_password")),
    }
