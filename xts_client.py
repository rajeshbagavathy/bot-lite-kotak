import datetime
import json
import logging
import re
import threading
from typing import Any, Dict, List, Optional

import pytz

from Connect import XTSConnect
from config import IndexConfig, MARKETABLE_LIMIT_SLIPPAGE_PCT

logger = logging.getLogger(__name__)

# XTS intraday OHLC: 1-minute interval per Symphony Marketdata API (compression = seconds).
OHLC_COMPRESSION_1_MINUTE = 60

_IST = pytz.timezone("Asia/Kolkata")


def round_to_tick(price: float, tick_size: float) -> float:
    if tick_size and tick_size > 0:
        return round(price / tick_size) * tick_size
    return price


def marketable_limit_price(
    ltp: float,
    order_side: str,
    slippage_pct: float,
    tick_size: float,
) -> float:
    """BUY: LTP * (1 + s); SELL: LTP * (1 - s). ``slippage_pct`` is fractional (0.01 = 1%)."""
    side = (order_side or "").strip().upper()
    if side == XTSConnect.TRANSACTION_TYPE_BUY:
        raw = ltp * (1 + slippage_pct)
    else:
        raw = ltp * (1 - slippage_pct)
    return round_to_tick(raw, tick_size)


class XTSClient:
    def __init__(self, api_key: str, api_secret: str, market_api_key: str, market_api_secret: str, source: str, client_id: str):
        self.client_id = client_id
        self.interactive = XTSConnect(api_key, api_secret, source)
        self.market = XTSConnect(market_api_key, market_api_secret, source)
        # Serialize REST market calls that may overlap with touchline subscription on the same session.
        self._market_api_lock = threading.Lock()

    def login(self) -> None:
        self.interactive.interactive_login()
        self.market.marketdata_login()

    def get_expiry_dates(self, index_config: IndexConfig) -> List[datetime.datetime]:
        # XTS expiry API expects exchangeSegment as numeric (e.g., 2) and series as OPTIDX/IO.
        logger.debug(
            "API Call: get_expiry_date(segment=%s, series=%s, symbol=%s)",
            index_config.option_ltp_segment,
            index_config.option_exchange_segment,
            index_config.fno_symbol,
        )
        result = self.market.get_expiry_date(
            index_config.option_ltp_segment,
            index_config.option_exchange_segment,
            index_config.fno_symbol,
        )
        logger.debug(f"API Response for {index_config.name}: {result}")
        expiries = []
        if result and result.get("result"):
            for expiry in result.get("result"):
                try:
                    expiries.append(datetime.datetime.strptime(expiry, "%Y-%m-%dT%H:%M:%S"))
                except ValueError:
                    logger.warning("Unexpected expiry format: %s", expiry)
        expiries.sort()
        logger.debug(f"Parsed expiries for {index_config.name}: {expiries}")
        return expiries

    def format_expiry_for_options(self, expiry: datetime.datetime) -> str:
        return expiry.strftime("%d%b%Y")

    def get_option_instrument_id(self, index_config: IndexConfig, expiry: str, option_type: str, strike: int) -> Optional[int]:
        result = self.market.get_option_symbol(
            index_config.option_ltp_segment,
            index_config.option_exchange_segment,
            index_config.fno_symbol,
            expiry,
            option_type,
            strike,
        )
        if result and result.get("result"):
            return result.get("result")[0].get("ExchangeInstrumentID")
        return None

    def get_positions(self) -> List[dict]:
        result = self.interactive.get_position_daywise(self.client_id)
        if result and result.get("result") and result.get("result").get("positionList"):
            return result.get("result").get("positionList")
        return []

    def get_order_book(self) -> List[dict]:
        result = self.interactive.get_order_book(self.client_id)
        if result and result.get("result"):
            return result.get("result")
        return []

    def get_available_margin(self) -> Optional[float]:
        result = self.interactive.get_balance(self.client_id)
        if not result or not result.get("result"):
            return None
        balance_list = result.get("result", {}).get("BalanceList") or []
        if not balance_list:
            return None
        limit_obj = balance_list[0].get("limitObject") or {}
        rms_limits = limit_obj.get("RMSSubLimits") or {}
        available = rms_limits.get("netMarginAvailable")
        try:
            return round(float(available))
        except (TypeError, ValueError):
            return None

    def get_ltp_map(self, instruments: List[dict]) -> Dict[int, float]:
        ltp_map: Dict[int, float] = {}
        if not instruments:
            return ltp_map
        try:
            response = self.market.send_subscription(Instruments=instruments, xtsMessageCode=1502)
            if response and response.get("result"):
                quotes = response.get("result").get("listQuotes") or []
                for quote in quotes:
                    quote_obj = json.loads(quote)
                    instrument_id = quote_obj.get("ExchangeInstrumentID")
                    ltp = quote_obj.get("Touchline", {}).get("LastTradedPrice")
                    if instrument_id is not None and ltp is not None:
                        ltp_map[int(instrument_id)] = ltp
        finally:
            self.market.send_unsubscription(Instruments=instruments, xtsMessageCode=1502)
        return ltp_map

    @staticmethod
    def format_ohlc_request_time(dt: datetime.datetime) -> str:
        """XTS expects e.g. ``Apr 08 2026 091500`` (MMM DD YYYY HHMMSS) in local session timezone; we use IST."""
        if dt.tzinfo is None:
            dt = _IST.localize(dt)
        else:
            dt = dt.astimezone(_IST)
        return dt.strftime("%b %d %Y %H%M%S")

    @staticmethod
    def parse_ohlc_data_response(data_response: str) -> List[Dict[str, Any]]:
        """
        Parse ``dataReponse`` payload: bars of ``unix_ts|open|high|low|close|volume|...``.
        XTS payload may be newline-separated and/or comma-separated depending on route/version.
        Returns bar dicts with bar_unix (int), open, high, low, close, volume.
        """
        if not data_response or not str(data_response).strip():
            return []
        out: List[Dict[str, Any]] = []
        chunks = re.split(r"[\r\n,]+", str(data_response).strip())
        for line in chunks:
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) < 5:
                continue
            try:
                ts = int(float(parts[0]))
                o, h, l, c = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
                vol: Optional[float] = None
                if len(parts) > 5 and parts[5] not in ("", None):
                    try:
                        vol = float(parts[5])
                    except (TypeError, ValueError):
                        vol = None
            except (TypeError, ValueError) as e:
                logger.debug("Skipping OHLC line %r: %s", line, e)
                continue
            out.append(
                {
                    "bar_unix": ts,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": vol,
                }
            )
        return out

    def get_spot_ohlc_bars(
        self,
        index_config: IndexConfig,
        start: datetime.datetime,
        end: datetime.datetime,
        compression_seconds: int = OHLC_COMPRESSION_1_MINUTE,
    ) -> List[Dict[str, Any]]:
        """
        Fetch intraday OHLC for the index spot instrument. Times should be in IST (naive or aware).
        """
        st = self.format_ohlc_request_time(start)
        et = self.format_ohlc_request_time(end)
        with self._market_api_lock:
            raw = self.market.get_ohlc(
                index_config.spot_exchange_segment,
                index_config.spot_instrument_id,
                st,
                et,
                compression_seconds,
            )
        if not raw or not isinstance(raw, dict):
            logger.warning("OHLC unexpected response type for %s", index_config.name)
            return []
        result = raw.get("result")
        if result is None:
            err = raw.get("description") or raw.get("message")
            if err:
                logger.warning("OHLC no result for %s: %s", index_config.name, err)
            return []
        payload = ""
        if isinstance(result, dict):
            payload = result.get("dataReponse") or result.get("dataResponse") or ""
        elif isinstance(result, str):
            payload = result
        if not payload:
            logger.debug("OHLC empty dataReponse for %s: %s", index_config.name, raw)
            return []
        bars = self.parse_ohlc_data_response(str(payload))
        for b in bars:
            b["index_name"] = index_config.name
        return bars

    def get_spot_ltp(self, index_config: IndexConfig) -> Optional[float]:
        instruments = [
            {
                "exchangeSegment": index_config.spot_exchange_segment,
                "exchangeInstrumentID": index_config.spot_instrument_id,
            }
        ]
        ltp_map = self.get_ltp_map(instruments)
        return ltp_map.get(index_config.spot_instrument_id)

    def get_option_ltp(self, index_config: IndexConfig, instrument_id: int) -> Optional[float]:
        instruments = [
            {"exchangeSegment": index_config.option_ltp_segment, "exchangeInstrumentID": instrument_id}
        ]
        ltp_map = self.get_ltp_map(instruments)
        ltp = ltp_map.get(int(instrument_id))
        if ltp is None:
            return None
        try:
            return float(ltp)
        except (TypeError, ValueError):
            return None

    def place_market_order(
        self,
        index_config: IndexConfig,
        instrument_id: int,
        order_side: str,
        quantity: int,
        tag: str,
        product_type: str,
        ltp: Optional[float] = None,
        slippage_pct: Optional[float] = None,
    ) -> Optional[int]:
        """
        Place a marketable LIMIT order (LTP +/- slippage, tick-rounded). ``ltp`` optional when caller batched quotes.
        """
        s = MARKETABLE_LIMIT_SLIPPAGE_PCT if slippage_pct is None else slippage_pct
        ltp_val = ltp if ltp is not None else self.get_option_ltp(index_config, instrument_id)
        if ltp_val is None:
            logger.warning("No LTP for instrument %s; cannot place marketable limit order", instrument_id)
            return None
        limit_price = marketable_limit_price(
            float(ltp_val),
            order_side,
            s,
            float(index_config.tick_size),
        )
        response = self.interactive.place_order(
            exchangeSegment=index_config.order_exchange_segment,
            exchangeInstrumentID=instrument_id,
            productType=product_type,
            orderType=self.interactive.ORDER_TYPE_LIMIT,
            orderSide=order_side,
            timeInForce=self.interactive.VALIDITY_DAY,
            disclosedQuantity=0,
            orderQuantity=quantity,
            limitPrice=float(limit_price),
            stopPrice=0,
            orderUniqueIdentifier=tag,
            clientID=self.client_id,
        )
        if response and response.get("result"):
            return response.get("result").get("AppOrderID")
        return None

    def place_sl_order(
        self,
        index_config: IndexConfig,
        instrument_id: int,
        order_side: str,
        quantity: int,
        limit_price: float,
        stop_price: float,
        tag: str,
        product_type: str,
    ) -> Optional[int]:
        response = self.interactive.place_order(
            exchangeSegment=index_config.order_exchange_segment,
            exchangeInstrumentID=instrument_id,
            productType=product_type,
            orderType=self.interactive.ORDER_TYPE_STOPLIMIT,
            orderSide=order_side,
            timeInForce=self.interactive.VALIDITY_DAY,
            disclosedQuantity=0,
            orderQuantity=quantity,
            limitPrice=float(round(limit_price, 2)),
            stopPrice=float(round(stop_price, 2)),
            orderUniqueIdentifier=tag,
            clientID=self.client_id,
        )
        if response and response.get("result"):
            return response.get("result").get("AppOrderID")
        return None

    def cancel_order(self, app_order_id: int, tag: str) -> None:
        self.interactive.cancel_order(app_order_id, tag, self.client_id)

    def cancel_all_orders(self, index_config: IndexConfig, instrument_id: int) -> None:
        self.interactive.cancelall_order(index_config.order_exchange_segment, instrument_id)

    def modify_order(
        self,
        app_order_id: int,
        product_type: str,
        order_type: str,
        quantity: int,
        disclosed_quantity: int,
        stop_price: float,
        limit_price: float,
        time_in_force: str,
        tag: str,
    ) -> Any:
        """
        Modify an existing order (e.g., convert SL to marketable LIMIT execution).

        Connect.modify_order(...) expects XTS params in this order (see Connect.py):
        modifiedLimitPrice, modifiedStopPrice — same as typical xt.modify_order(modifiedLimitPrice=..., modifiedStopPrice=...).

        Args:
            app_order_id: AppOrderID of the order to modify
            product_type: Product type (MIS, CNC, etc.)
            order_type: New order type (LIMIT, STOPLIMIT, etc.)
            quantity: Order quantity
            disclosed_quantity: Disclosed quantity
            limit_price: Limit price for LIMIT; limit leg for STOPLIMIT
            stop_price: Stop/trigger leg (STOPLIMIT); use 0 for plain LIMIT
            time_in_force: Time in force (DAY, etc.)
            tag: Order unique identifier

        Returns:
            Raw API response dict from Connect (or error string on some failures). Caller should check ``result``.
        """
        return self.interactive.modify_order(
            app_order_id,
            product_type,
            order_type,
            quantity,
            disclosed_quantity,
            limit_price,
            stop_price,
            time_in_force,
            tag,
            self.client_id,
        )
