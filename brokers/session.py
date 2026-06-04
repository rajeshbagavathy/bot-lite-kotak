"""Kotak Neo session: TOTP + MPIN, optional renewal on demand."""

from __future__ import annotations

import binascii
import logging
import os
import time
import urllib.parse
from typing import Any, Callable, Optional

logger = logging.getLogger("xts-bot-lite")

try:
    import pyotp
except ImportError:
    pyotp = None


def _normalize_totp_secret(raw: Optional[str]) -> Optional[str]:
    """
    Kotak / Google Authenticator secrets are Base32 (letters A–Z and digits 2–7 only).
    Accepts optional otpauth:// URI; strips spaces, dashes, quotes, padding.
    """
    if not raw:
        return None
    s = raw.strip().strip('"').strip("'")
    if s.lower().startswith("otpauth://"):
        parsed = urllib.parse.urlparse(s)
        qs = urllib.parse.parse_qs(parsed.query)
        vals = qs.get("secret") or []
        if vals:
            s = vals[0]
        else:
            return None
    s = s.replace(" ", "").replace("-", "").upper()
    if not s:
        return None
    return s


class KotakSessionManager:
    """
    Wraps NeoAPI login steps. Call ``ensure()`` before trading API usage.

    Environment (optional):
    - ``KOTAK_SESSION_MAX_AGE_SEC``: force re-login after N seconds (0 = disabled).
    """

    def __init__(
        self,
        api: Any,
        mobile_number: str,
        ucc: str,
        mpin: str,
        totp_secret: Optional[str] = None,
        totp_fn: Optional[Callable[[], str]] = None,
    ):
        self._api = api
        self._mobile = mobile_number
        self._ucc = ucc
        self._mpin = mpin
        self._totp_secret = _normalize_totp_secret(totp_secret)
        self._totp_fn = totp_fn
        self._last_login_ts: float = 0.0

    def _gen_totp(self) -> str:
        if self._totp_fn:
            return str(self._totp_fn()).strip()
        # Prefer Base32 secret so every process gets a *fresh* code. KOTAK_TOTP in .env is one-shot (~30s)
        # and breaks a second terminal/script while the bot is already running.
        if self._totp_secret and pyotp:
            try:
                return pyotp.TOTP(self._totp_secret).now()
            except binascii.Error as e:
                raise RuntimeError(
                    "Invalid KOTAK_TOTP_SECRET (Base32 decode failed). "
                    "Paste the secret key from Google Authenticator / Kotak (often shown when you add the account), "
                    "not the 6-digit rolling code. "
                    "For a quick test you can set KOTAK_TOTP to the current 6-digit code instead."
                ) from e
        # One-shot code from env (local test only). Ignored when dashboard TOTP flow is enabled.
        manual = ""
        if not self._dashboard_totp_mode():
            manual = (os.getenv("KOTAK_TOTP") or "").strip().replace(" ", "")
        if manual:
            if not manual.isdigit() or len(manual) != 6:
                raise RuntimeError(
                    "KOTAK_TOTP must be exactly 6 digits (current code from your authenticator app)."
                )
            logger.warning(
                "Using KOTAK_TOTP from env (one-shot). For automation set KOTAK_TOTP_SECRET (Base32 key)."
            )
            return manual
        raise RuntimeError(
            "Kotak TOTP required: install pyotp, set KOTAK_TOTP_SECRET (Base32 setup key), "
            "or set KOTAK_TOTP (6-digit code, one-shot) or pass totp_fn"
        )

    def login_with_totp(self, totp: str) -> None:
        """Establish session using a user-supplied 6-digit TOTP (dashboard / one-shot)."""
        code = str(totp).strip().replace(" ", "")
        if not code.isdigit() or len(code) != 6:
            raise RuntimeError("TOTP must be exactly 6 digits")
        self._totp_login_validate(code)

    def login(self) -> None:
        totp = self._gen_totp()
        self._totp_login_validate(totp)

    def _totp_login_validate(self, totp: str) -> None:
        r1 = self._api.totp_login(mobile_number=self._mobile, ucc=self._ucc, totp=totp)
        if isinstance(r1, dict) and r1.get("error"):
            hint = ""
            raw = str(r1)
            if "10506" in raw or "invalid totp" in raw.lower():
                hint = (
                    " Stale or reused 6-digit code: if .env has KOTAK_TOTP, it expires in ~30s—use "
                    "KOTAK_TOTP_SECRET (recommended), or run with a fresh code: "
                    "KOTAK_TOTP=123456 python3 … / scripts/debug_kotak_quotes.py --totp 123456."
                )
            raise RuntimeError(f"Kotak totp_login failed: {r1}{hint}")
        r2 = self._api.totp_validate(mpin=self._mpin)
        if isinstance(r2, dict) and (r2.get("error") or r2.get("Error")):
            raise RuntimeError(f"Kotak totp_validate failed: {r2}")
        cfg = self._api.configuration
        if not cfg.edit_token or not cfg.edit_sid:
            raise RuntimeError("Kotak session missing edit_token/edit_sid after totp_validate")
        self._last_login_ts = time.time()
        logger.info("Kotak Neo session established (ucc=%s)", self._ucc)
        # Order API can return stCode 100008 unauthorized for a few seconds right after login.
        time.sleep(2.0)

    @staticmethod
    def _dashboard_totp_mode() -> bool:
        try:
            import kotak_auth

            return kotak_auth.kotak_ui_totp_enabled()
        except Exception:
            return False

    def ensure(self) -> None:
        max_age = int(os.getenv("KOTAK_SESSION_MAX_AGE_SEC", "0") or "0")
        stale = max_age > 0 and (time.time() - self._last_login_ts) > max_age
        cfg = self._api.configuration
        if stale or not cfg.edit_token or not cfg.edit_sid:
            if self._dashboard_totp_mode():
                from kotak_auth import KotakSessionNotReady

                raise KotakSessionNotReady(
                    "Kotak session not ready — enter today's TOTP in the dashboard."
                )
            self.login()

    def current_server_id(self) -> Optional[str]:
        return getattr(self._api.configuration, "serverId", None)
