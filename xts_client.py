import datetime
import json
import logging
from typing import Dict, List, Optional

from Connect import XTSConnect
from config import IndexConfig

logger = logging.getLogger(__name__)


class XTSClient:
    def __init__(self, api_key: str, api_secret: str, market_api_key: str, market_api_secret: str, source: str, client_id: str):
        self.client_id = client_id
        self.interactive = XTSConnect(api_key, api_secret, source)
        self.market = XTSConnect(market_api_key, market_api_secret, source)

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

    def get_spot_ltp(self, index_config: IndexConfig) -> Optional[float]:
        instruments = [
            {
                "exchangeSegment": index_config.spot_exchange_segment,
                "exchangeInstrumentID": index_config.spot_instrument_id,
            }
        ]
        ltp_map = self.get_ltp_map(instruments)
        return ltp_map.get(index_config.spot_instrument_id)

    def place_market_order(
        self,
        index_config: IndexConfig,
        instrument_id: int,
        order_side: str,
        quantity: int,
        tag: str,
        product_type: str,
    ) -> Optional[int]:
        response = self.interactive.place_order(
            exchangeSegment=index_config.order_exchange_segment,
            exchangeInstrumentID=instrument_id,
            productType=product_type,
            orderType=self.interactive.ORDER_TYPE_MARKET,
            orderSide=order_side,
            timeInForce=self.interactive.VALIDITY_DAY,
            disclosedQuantity=0,
            orderQuantity=quantity,
            limitPrice=0,
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
