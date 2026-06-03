"""Order/trade constants shared across brokers (XTS-compatible names for strategy code)."""


class InteractiveConstants:
    """Subset of XTSConnect constants used by bot.py via ``client.interactive``."""

    TRANSACTION_TYPE_BUY = "BUY"
    TRANSACTION_TYPE_SELL = "SELL"
    ORDER_TYPE_LIMIT = "LIMIT"
    ORDER_TYPE_STOPLIMIT = "STOPLIMIT"
    ORDER_TYPE_MARKET = "MARKET"
    PRODUCT_MIS = "MIS"
    PRODUCT_NRML = "NRML"
    VALIDITY_DAY = "DAY"
