"""Kotak TOTP entry via dashboard (once per IST day)."""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Callable, Dict, Optional

from config import BROKER_BACKEND, DEMO_MODE
from db import get_ist_date, is_kotak_totp_satisfied_today, mark_kotak_totp_satisfied_today

logger = logging.getLogger("xts-bot-lite")


class KotakSessionNotReady(RuntimeError):
    """Kotak API session not established yet (dashboard TOTP pending)."""


_lock = threading.RLock()
_client: Any = None
_bootstrap_cb: Optional[Callable[[], None]] = None
_has_auto_totp_secret: bool = False
_login_in_progress: bool = False
_login_error: Optional[str] = None


def init_kotak_auth_mode(*, has_auto_totp_secret: bool) -> None:
    global _has_auto_totp_secret
    _has_auto_totp_secret = bool(has_auto_totp_secret)


def kotak_ui_totp_enabled() -> bool:
    if DEMO_MODE or BROKER_BACKEND != "kotak":
        return False
    if os.getenv("KOTAK_TOTP_UI", "true").lower() in ("false", "0", "no"):
        return False
    if _has_auto_totp_secret and os.getenv("KOTAK_FORCE_UI_TOTP", "").lower() not in (
        "true",
        "1",
        "yes",
    ):
        return False
    return True


def register_pending_client(client: Any, bootstrap_cb: Callable[[], None]) -> None:
    global _client, _bootstrap_cb
    with _lock:
        _client = client
        _bootstrap_cb = bootstrap_cb


def get_client() -> Any:
    with _lock:
        return _client


def client_session_active() -> bool:
    with _lock:
        if _client is None:
            return False
        try:
            cfg = _client._api.configuration
            return bool(getattr(cfg, "edit_token", None) and getattr(cfg, "edit_sid", None))
        except Exception:
            return False


def get_status() -> Dict[str, Any]:
    ist = get_ist_date()
    required = kotak_ui_totp_enabled()
    satisfied = is_kotak_totp_satisfied_today()
    active = client_session_active()
    with _lock:
        in_progress = _login_in_progress
        err = _login_error
    # Prompt when broker session is missing. Same browser refresh is OK if session_active.
    # satisfied_today is informational only (DB); bot restart needs a new TOTP even same IST day.
    needs = required and not active
    return {
        "broker": "kotak",
        "ist_date": ist,
        "ui_required": required,
        "satisfied_today": satisfied,
        "session_active": active,
        "needs_totp_entry": needs,
        "login_in_progress": in_progress,
        "login_error": err,
    }


def _run_totp_login(totp: str) -> None:
    global _login_in_progress, _login_error
    logger.info("Kotak TOTP background login started")
    try:
        with _lock:
            client = _client
            bootstrap = _bootstrap_cb
        if client is None:
            raise RuntimeError("Trading client not ready; restart the bot.")
        logger.info("Kotak TOTP: calling broker login…")
        client.login_with_totp(totp)
        mark_kotak_totp_satisfied_today()
        logger.info("Kotak TOTP: broker login OK; running bootstrap…")
        if bootstrap:
            bootstrap()
        logger.info("Kotak dashboard TOTP login and bootstrap finished")
    except Exception as e:
        logger.error("Kotak TOTP login failed: %s", e)
        with _lock:
            _login_error = str(e)
    finally:
        with _lock:
            _login_in_progress = False


def submit_totp(code: str) -> Dict[str, Any]:
    """Start Kotak login in a background thread (Flask must not block on scrip search)."""
    global _login_in_progress, _login_error

    totp = re.sub(r"\s+", "", (code or "").strip())
    if not totp.isdigit() or len(totp) != 6:
        return {"ok": False, "error": "Enter the 6-digit code from your authenticator app."}

    session_active = client_session_active()
    with _lock:
        if _client is None:
            return {"ok": False, "error": "Trading client not ready; restart the bot."}
        if kotak_ui_totp_enabled() and session_active:
            return {"ok": True, "already": True, "ist_date": get_ist_date()}
        if _login_in_progress:
            return {
                "ok": True,
                "pending": True,
                "message": "Kotak login already in progress…",
                "ist_date": get_ist_date(),
            }
        _login_in_progress = True
        _login_error = None

    logger.info("Kotak TOTP accepted from dashboard; starting background login thread")
    threading.Thread(target=_run_totp_login, args=(totp,), name="KotakTotpLogin", daemon=True).start()
    return {
        "ok": True,
        "pending": True,
        "message": "Connecting to Kotak (may take up to a minute)…",
        "ist_date": get_ist_date(),
    }
