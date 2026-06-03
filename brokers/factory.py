"""Construct the live trading client (XTS or Kotak) from environment / SSM."""

from __future__ import annotations

import os

from config import SOURCE, load_credentials, load_kotak_credentials

from xts_client import XTSClient


def create_trading_client():
    """
    ``BROKER_BACKEND`` env: ``xts`` (default) or ``kotak``.

    Kotak requires ``load_kotak_credentials()``; XTS uses ``load_credentials()`` + ``SOURCE``.
    """
    backend = os.getenv("BROKER_BACKEND", "xts").strip().lower()
    if backend == "kotak":
        from brokers.kotak_client import KotakNeoClient

        return KotakNeoClient.from_config(load_kotak_credentials())
    creds = load_credentials()
    return XTSClient(
        api_key=creds["api_key"],
        api_secret=creds["api_secret"],
        market_api_key=creds["market_api_key"],
        market_api_secret=creds["market_api_secret"],
        source=SOURCE,
        client_id=creds["client_id"],
    )
