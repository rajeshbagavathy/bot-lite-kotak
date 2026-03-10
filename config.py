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

STRATEGIES = [
    StrategyConfig("S0930", "09:30:00", 7, 20.0, 10000.0),
    StrategyConfig("S0950", "09:50:00", 7, 30.0, 10000.0),
    StrategyConfig("S1005", "10:05:00", 7, 30.0, 10000.0),
    StrategyConfig("S1025", "10:25:00", 7, 30.0, 10000.0),
    StrategyConfig("S1045", "10:45:00", 7, 35.0, 10000.0),
    StrategyConfig("S1144", "11:44:00", 7, 35.0, 10000.0),
    StrategyConfig("S1255", "12:55:00", 7, 35.0, 10000.0),
]

PORTFOLIO_SL_LIMIT = -80000.0
# Set to True to enable trading on non-expiry days; False disables all strategies on non-expiry.
TRADE_NON_EXPIRY_DAY = os.getenv("TRADE_NON_EXPIRY_DAY", "False").lower() in ("true", "1", "yes")

# Margin + hedging configuration
# If available margin is below this, bot will buy far-OTM hedges first.
REQUIRED_MARGIN_PER_STRATEGY = float(os.getenv("REQUIRED_MARGIN_PER_STRATEGY", "1750000"))
# Hedge lots to buy on both sides (CE+PE) when margin is low.
HEDGE_LOTS = int(os.getenv("HEDGE_LOTS", "7"))
# Strategy lots: expiry uses STRATEGIES[].lots (7), non-expiry uses this.
STRATEGY_LOTS_NON_EXPIRY = int(os.getenv("STRATEGY_LOTS_NON_EXPIRY", "4"))
# ITM strikes away from ATM for non-expiry (NIFTY: 2 → 25100 CE / 25300 PE @ spot 25200; SENSEX: 3).
ITM_STRIKES_NIFTY = int(os.getenv("ITM_STRIKES_NIFTY", "2"))
ITM_STRIKES_SENSEX = int(os.getenv("ITM_STRIKES_SENSEX", "3"))
# Leg SL % on non-expiry day (override per-strategy); expiry uses strategy leg_sl_pct.
LEG_SL_PCT_NON_EXPIRY = float(os.getenv("LEG_SL_PCT_NON_EXPIRY", "20.0"))
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
