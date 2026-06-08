"""Resolve symbols from ``bot`` when tests patch ``bot.*``; else use module default."""
from __future__ import annotations

import sys
from typing import Any


def resolve(name: str, fallback: Any) -> Any:
    bot = sys.modules.get("bot")
    if bot is not None and hasattr(bot, name):
        return getattr(bot, name)
    return fallback
