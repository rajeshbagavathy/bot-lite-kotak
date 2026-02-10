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
    StrategyConfig("S0920", "09:20:00", 1, 20.0, 16000.0),
    StrategyConfig("S1001", "11:12:00", 1, 20.0, 30000.0),
    StrategyConfig("S1240", "12:40:00", 1, 35.0, 16000.0),
    StrategyConfig("S1350", "13:50:00", 1, 35.0, 16000.0),
]

PORTFOLIO_SL_LIMIT = -80000.0
SOURCE = "WEBAPI"
DEMO_MODE = os.getenv("DEMO_MODE", "False").lower() in ("true", "1", "yes")  # Reads from environment variable
SSM_BASE_PATH = "/trade/config"
ACC_NAME = os.getenv("ACC_NAME")


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
        "api_key": _get_env_or_ssm("XTS_API_KEY_5P", "apikey"),
        "api_secret": _get_env_or_ssm("XTS_API_SECRET_5P", "apisecret"),
        "client_id": _get_env_or_ssm("XTS_5P_CLIENTID_5P", "clientid"),
        "market_api_key": _get_env_or_ssm("XTS_MARKET_API_KEY_5P", "marketdataapikey"),
        "market_api_secret": _get_env_or_ssm("XTS_MARKET_API_SECRET_5P", "marketdataapisecret"),
        "login_username": _get_env_or_ssm("LOGIN_USERNAME_5P", "loginusername"),
        "login_password": _get_env_or_ssm("LOGIN_PASSWORD_5P", "loginpassword"),
    }


def get_basic_auth_creds(creds: dict) -> dict:
    return {
        "username": os.getenv("BASIC_AUTH_USERNAME", creds.get("login_username")),
        "password": os.getenv("BASIC_AUTH_PASSWORD", creds.get("login_password")),
    }
