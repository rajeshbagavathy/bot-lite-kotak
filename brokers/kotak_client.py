"""
Kotak Neo API trading client with the same surface as ``XTSClient`` where ``bot.py`` expects it.

Requires the vendored SDK under ``Kotak-neo-api-v2/`` on ``sys.path``.
"""

from __future__ import annotations

import datetime
import logging
import os
import sys
import time
import urllib.parse
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytz

from config import CALM_ZONE_BAR_UNIX_OFFSET_SEC, IndexConfig, MARKETABLE_LIMIT_SLIPPAGE_PCT

from brokers.constants import InteractiveConstants
from brokers.mappers.kotak_normalize import (
    kotak_order_to_normalized,
    kotak_positions_to_normalized,
    parse_kotak_place_order_n_ord_no,
)
from brokers.session import KotakSessionManager

logger = logging.getLogger("xts-bot-lite")

_KOTAK_ROOT = Path(__file__).resolve().parent.parent / "Kotak-neo-api-v2"
if _KOTAK_ROOT.is_dir() and str(_KOTAK_ROOT) not in sys.path:
    sys.path.insert(0, str(_KOTAK_ROOT))

from neo_api_client import ModifyOrder, NeoAPI  # noqa: E402
from neo_api_client.settings import PROD_URL, UAT_URL  # noqa: E402

# XTS numeric segment (config.IndexConfig) -> Kotak ``exchange_segment`` for quotes/orders
XTS_EXCHANGE_SEGMENT_TO_KOTAK: Dict[int, str] = {
    1: "nse_cm",
    2: "nse_fo",
    11: "bse_cm",
    12: "bse_fo",
}

# Index-level Kotak metadata (FO segment, scrip search symbol, index quote name).
# ``spot_quote_token``: numeric ``pSymbol`` for the cash index row in Kotak scrip master (REST quotes
# work as ``nse_cm|26000`` etc.; string names like ``Nifty 50`` often return empty on trade API).
KOTAK_INDEX_META: Dict[str, Dict[str, Any]] = {
    "NIFTY": {
        "fo_seg": "nse_fo",
        "spot_seg": "nse_cm",
        "index_quote_name": "Nifty 50",
        "search_symbol": "nifty",
        # Kotak ``nse_cm`` master: pSymbol=26000, pTrdSymbol=NIFTY (quotes as ``nse_cm|26000`` or ``nse_cm|NIFTY``).
        "spot_quote_token": 26000,
        "spot_quote_symbol": "NIFTY",
    },
    "SENSEX": {
        "fo_seg": "bse_fo",
        "spot_seg": "bse_cm",
        "index_quote_name": "SENSEX",
        "search_symbol": "sensex",
        "spot_quote_symbol": "SENSEX",
    },
}

# If primary ``index_quote_name`` returns empty quotes, try these (Neo / exchange naming).
KOTAK_INDEX_QUOTE_FALLBACKS: Dict[str, List[str]] = {
    # e22 rejects ``NIFTY 50`` (case); ``Nifty 50`` is primary. Optional extra tries only.
    "NIFTY": ["Nifty50"],
    "SENSEX": ["BSE SENSEX"],
}


def _spot_bar_unix_for_db(true_epoch: int) -> int:
    """
    Calm zone stores vendor-ish unix: ``canonical_spot_bar_time_ist`` adds
    ``CALM_ZONE_BAR_UNIX_OFFSET_SEC`` before formatting. Kotak uses real POSIX
    seconds; shift so IST bar_time matches wall clock when the default offset is set.
    """
    return int(true_epoch) - int(CALM_ZONE_BAR_UNIX_OFFSET_SEC)


def _map_xts_order_type_to_kotak_pt(order_type: str) -> str:
    u = (order_type or "").strip().upper()
    if u in ("LIMIT", "L"):
        return "L"
    if u in ("MARKET", "MKT"):
        return "MKT"
    if u in ("STOPLIMIT", "SL"):
        return "SL"
    return "L"


class KotakNeoClient:
    """Mirror of ``XTSClient`` methods used by ``bot.py`` / ``calm_zone_service.py``."""

    def __init__(
        self,
        consumer_key: str,
        mobile_number: str,
        ucc: str,
        mpin: str,
        environment: str = "prod",
        neo_fin_key: Optional[str] = None,
        totp_secret: Optional[str] = None,
    ):
        self.interactive = InteractiveConstants()
        self._consumer_key = consumer_key
        self._api = NeoAPI(
            environment=environment,
            access_token=None,
            neo_fin_key=neo_fin_key,
            consumer_key=consumer_key,
        )
        self._session = KotakSessionManager(
            self._api,
            mobile_number=mobile_number,
            ucc=ucc,
            mpin=mpin,
            totp_secret=totp_secret,
        )
        # token (int) -> {"trdSym": str, "segment": str}
        self._token_meta: Dict[int, Dict[str, str]] = {}
        # index name -> recent (unix_ts, ltp) for synthetic 1m bars when REST OHLC is empty
        self._index_ltp_ticks: Dict[str, List[Tuple[int, float]]] = defaultdict(list)
        self._ltp_history_cap = 720
        # ``None`` = not resolved yet; ``-1`` = search failed; else BSE SENSEX ``pSymbol`` for quotes
        self._sensex_spot_tok_cache: Optional[int] = None

    @staticmethod
    def _valid_spot_index_token(tok: int) -> bool:
        """Reject bogus matches (e.g. substring search hitting pSymbol 1)."""
        return tok >= 1000

    def _sensex_numeric_spot_token(self) -> Optional[int]:
        """BSE SENSEX index ``pSymbol`` via scrip search (not bundled in repo CSV)."""
        if self._sensex_spot_tok_cache is not None:
            return self._sensex_spot_tok_cache if self._sensex_spot_tok_cache > 0 else None
        env_tok = (os.getenv("KOTAK_SENSEX_SPOT_TOKEN") or "").strip()
        if env_tok.isdigit():
            t = int(env_tok)
            if self._valid_spot_index_token(t):
                self._sensex_spot_tok_cache = t
                logger.info("Kotak: BSE SENSEX spot token from KOTAK_SENSEX_SPOT_TOKEN=%s", env_tok)
                return t
            logger.warning("Kotak: KOTAK_SENSEX_SPOT_TOKEN=%s looks invalid; ignoring", env_tok)
        self._ensure()
        found: Optional[int] = None
        rows = self._api.search_scrip(
            exchange_segment="bse_cm",
            symbol="sensex",
            expiry="",
            option_type="",
            strike_price="",
        )
        if isinstance(rows, list):
            candidates: List[Tuple[int, int]] = []  # (score, token)
            for row in rows:
                if not isinstance(row, dict):
                    continue
                pname = str(row.get("pSymbolName") or "").strip().upper()
                tsym = str(row.get("pTrdSymbol") or "").strip().upper()
                tok_raw = row.get("pSymbol")
                if tok_raw is None:
                    continue
                try:
                    tok = int(float(tok_raw))
                except (TypeError, ValueError):
                    continue
                if not self._valid_spot_index_token(tok):
                    continue
                # ``search_scrip`` uses substring match on pSymbolName — require a real index row.
                if tsym == "SENSEX" and pname == "SENSEX":
                    candidates.append((100, tok))
                elif tsym == "SENSEX" and "-" not in tsym and "ETF" not in pname:
                    candidates.append((80, tok))
                elif pname == "SENSEX" and "-EQ" not in tsym and "-BL" not in tsym and "ETF" not in tsym:
                    candidates.append((60, tok))
            if candidates:
                candidates.sort(key=lambda x: (-x[0], -x[1]))
                found = candidates[0][1]
        self._sensex_spot_tok_cache = found if found is not None else -1
        if found:
            logger.info("Kotak: using BSE SENSEX spot quote token %s (from scrip search)", found)
        else:
            logger.warning(
                "Kotak: BSE SENSEX spot token not resolved; set KOTAK_SENSEX_SPOT_TOKEN or use name-only quotes"
            )
        return found

    def _quotes_headers(self) -> Dict[str, str]:
        cfg = self._api.configuration
        h: Dict[str, str] = {
            "Authorization": str(self._consumer_key or cfg.consumer_key or ""),
            "Sid": str(cfg.edit_sid or ""),
            "Auth": str(cfg.edit_token or ""),
            "neo-fin-key": str(cfg.get_neo_fin_key()),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        return h

    def _quote_url_templates(self) -> List[str]:
        host = (self._api.configuration.host or "prod").lower().strip()
        raw: List[str] = []
        if host == "prod":
            raw.append(PROD_URL.get("quotes_neo_symbol") or "")
            napi = PROD_URL.get("quotes_neo_symbol_napi") or ""
            if napi:
                raw.append(napi)
        else:
            raw.append(UAT_URL.get("quotes_neo_symbol") or "")
        out: List[str] = []
        seen: set = set()
        for p in raw:
            p = (p or "").strip().lstrip("/")
            if p and p not in seen:
                seen.add(p)
                out.append(p)
        return out

    def _quotes_get(
        self,
        instrument_tokens: List[dict],
        quote_type: Optional[str],
        *,
        is_index: bool = False,
    ) -> Any:
        """
        REST quotes with **session** headers (Sid/Auth + neo-fin-key), same family as limits/orders.
        ``is_index=True`` adds ``isIndex=true`` (required for Nifty 50 / SENSEX name tokens per Kotak docs).
        Retries with consumer-key headers, optional ``sId`` on/off, then SDK ``quotes()``.
        """
        if not instrument_tokens:
            return {"error": [{"message": "instrument_tokens are missing"}]}
        qt = quote_type or "all"
        neo_symbol_str = ",".join(
            f"{item['exchange_segment']}|{item['instrument_token']}" for item in instrument_tokens
        )
        # Match vendored ``QuotesAPI.get_quotes`` (default ``quote``, not ``safe=""``).
        encoded = urllib.parse.quote(neo_symbol_str)
        cfg = self._api.configuration
        base = cfg.get_domain().rstrip("/")
        qp_base: Dict[str, str] = {}
        if is_index:
            qp_base["isIndex"] = "true"
        sid = getattr(cfg, "serverId", None)
        qp_variants: List[Optional[Dict[str, str]]] = []
        if sid:
            qp_variants.append({**qp_base, "sId": str(sid)})
        qp_variants.append(qp_base if qp_base else None)
        seen_qp: set = set()
        qp_list: List[Optional[Dict[str, str]]] = []
        for qpv in qp_variants:
            key = tuple(sorted((qpv or {}).items())) if qpv else ()
            if key in seen_qp:
                continue
            seen_qp.add(key)
            qp_list.append(qpv)
        last_err: Any = None
        last_payload: Any = None
        header_modes: List[Tuple[str, Dict[str, str]]] = [
            ("session", self._quotes_headers()),
            (
                "consumer",
                {
                    "Authorization": str(cfg.consumer_key or ""),
                    "neo-fin-key": str(cfg.get_neo_fin_key()),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            ),
        ]
        for qp in qp_list:
            for rel in self._quote_url_templates():
                url = f"{base}/{rel.format(neo_symbols=encoded, quote_type=qt)}"
                for hdr_label, headers in header_modes:
                    try:
                        resp = self._api.api_client.rest_client.request(
                            "GET", url, query_params=qp or None, headers=headers
                        )
                        if resp.status_code < 200 or resp.status_code > 299:
                            last_err = (resp.status_code, hdr_label, (resp.text or "")[:400])
                            continue
                        try:
                            payload = resp.json()
                        except Exception as e:
                            last_err = ("json", hdr_label, str(e), (resp.text or "")[:400])
                            continue
                        if isinstance(payload, list):
                            last_payload = payload
                            if self._quote_payload_has_useful_data(payload):
                                return payload
                            last_err = ("empty_quote", hdr_label, neo_symbol_str[:96])
                            continue
                        if isinstance(payload, dict):
                            if payload.get("fault"):
                                last_err = ("fault", hdr_label, str(payload.get("fault"))[:400])
                                continue
                            last_payload = payload
                            if payload.get("Error") or payload.get("error"):
                                last_err = ("api_error", hdr_label, str(payload)[:400])
                                continue
                            if self._quote_payload_has_useful_data(payload):
                                return payload
                            last_err = ("empty_quote", hdr_label, neo_symbol_str[:96])
                    except Exception as e:
                        last_err = (hdr_label, str(e))
                        continue
        sdk_payload = self._api.quotes(instrument_tokens=instrument_tokens, quote_type=qt)
        if self._quote_payload_has_useful_data(sdk_payload):
            return sdk_payload
        if isinstance(last_payload, (dict, list)):
            logger.debug(
                "Kotak quotes: REST had no parseable rows (%s), last_err=%s; using last REST body or SDK",
                neo_symbol_str[:96],
                last_err,
            )
            return last_payload
        logger.debug(
            "Kotak session/consumer quotes failed (%s), using SDK body: %s",
            neo_symbol_str[:96],
            last_err,
        )
        return sdk_payload

    def _quote_payload_has_useful_data(self, p: Any) -> bool:
        if isinstance(p, dict) and p.get("fault"):
            return False
        for row in self._quote_rows_from_response(p):
            if self._quote_row_first_price(row) is not None:
                return True
        acc: List[float] = []
        self._collect_first_quote_price(
            p, acc, ("iv", "ltp", "last_traded_price", "Ltp", "close", "lp", "buy_price")
        )
        return bool(acc)

    @staticmethod
    def _unwrap_limits_root(r: Dict[str, Any]) -> Dict[str, Any]:
        d = r.get("data")
        if isinstance(d, dict) and d:
            kl = {str(k).lower() for k in d}
            if (
                "net" in kl
                or "unrealizedmtomprsnt" in kl
                or "realizedmtomprsnt" in kl
                or "marginused" in kl
            ):
                return d
        return r

    @staticmethod
    def _limits_get_float(r: Dict[str, Any], *candidate_names: str) -> float:
        lk = {str(k).lower(): k for k in r}
        for name in candidate_names:
            key = lk.get(name.lower())
            if key is None:
                continue
            v = r.get(key)
            if v is None or v == "":
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return 0.0

    @classmethod
    def from_config(cls, cfg: Dict[str, Any]) -> "KotakNeoClient":
        return cls(
            consumer_key=str(cfg["consumer_key"]),
            mobile_number=str(cfg["mobile_number"]),
            ucc=str(cfg["ucc"]),
            mpin=str(cfg["mpin"]),
            environment=str(cfg.get("environment") or "prod"),
            neo_fin_key=cfg.get("neo_fin_key"),
            totp_secret=cfg.get("totp_secret"),
        )

    def login(self) -> None:
        self._session.login()

    def login_with_totp(self, totp: str) -> None:
        self._session.login_with_totp(totp)

    def is_session_active(self) -> bool:
        cfg = self._api.configuration
        return bool(getattr(cfg, "edit_token", None) and getattr(cfg, "edit_sid", None))

    def _ensure(self) -> None:
        self._session.ensure()

    @staticmethod
    def format_expiry_for_options(expiry: datetime.datetime) -> str:
        return expiry.strftime("%d%b%Y").upper()

    def get_expiry_dates(self, index_config: IndexConfig) -> List[datetime.datetime]:
        self._ensure()
        meta = KOTAK_INDEX_META.get(index_config.name)
        if not meta:
            return []
        rows = self._api.search_scrip(
            exchange_segment=meta["fo_seg"],
            symbol=meta["search_symbol"],
            expiry="",
            option_type="",
            strike_price="",
        )
        if not isinstance(rows, list):
            return []
        seen: set = set()
        out: List[datetime.datetime] = []
        for row in rows:
            raw = row.get("pExpiryDate") or row.get("lExpiryDate")
            if raw is None:
                continue
            s = str(raw).strip().upper()
            if not s or s in seen:
                continue
            seen.add(s)
            try:
                out.append(datetime.datetime.strptime(s, "%d%b%Y"))
            except ValueError:
                continue
        out.sort()
        return out

    def get_option_instrument_id(
        self, index_config: IndexConfig, expiry: str, option_type: str, strike: int
    ) -> Optional[int]:
        self._ensure()
        meta = KOTAK_INDEX_META.get(index_config.name)
        if not meta:
            return None
        exp = (expiry or "").strip().upper()
        rows = self._api.search_scrip(
            exchange_segment=meta["fo_seg"],
            symbol=meta["search_symbol"],
            expiry=exp,
            option_type=(option_type or "").lower(),
            strike_price=str(int(strike)),
        )
        if not isinstance(rows, list) or not rows:
            return None
        row = rows[0]
        tok = row.get("pSymbol")
        sym = row.get("pTrdSymbol") or ""
        if tok is None:
            return None
        tid = int(tok)
        self._token_meta[tid] = {"trdSym": sym, "segment": meta["fo_seg"]}
        return tid

    def get_positions(self) -> List[dict]:
        self._ensure()
        r = self._api.positions()
        if not isinstance(r, dict) or "data" not in r:
            return []
        data = r.get("data")
        if not isinstance(data, list):
            return []
        return kotak_positions_to_normalized(data)

    def get_order_book(self) -> List[dict]:
        self._ensure()
        r = self._api.order_report()
        if not isinstance(r, dict) or "data" not in r:
            if isinstance(r, dict) and r.get("Error"):
                logger.warning("Kotak order_report error: %s", r.get("Error"))
            return []
        data = r.get("data")
        if not isinstance(data, list):
            return []
        return [kotak_order_to_normalized(row) for row in data]

    def get_available_margin(self) -> Optional[float]:
        self._ensure()
        r = self._api.limits(segment="ALL", exchange="ALL", product="ALL")
        if not isinstance(r, dict):
            return None
        if r.get("Error"):
            logger.warning("Kotak limits error: %s", r.get("Error"))
            return None
        r = self._unwrap_limits_root(r)
        net = r.get("Net")
        if net is None:
            lk = {str(k).lower(): k for k in r}
            nk = lk.get("net")
            if nk is not None:
                net = r.get(nk)
        if net is None:
            return None
        try:
            return round(float(net))
        except (TypeError, ValueError):
            return None

    def get_portfolio_mtm_from_limits(self) -> Optional[Tuple[float, float, float]]:
        """
        RMS portfolio P&L from ``limits()`` — aggregate realized + unrealized MTM.

        Kotak docs / sample: ``UnrealizedMtomPrsnt``, ``RealizedMtomPrsnt``; some accounts
        populate segment ``*UnRlsMtomPrsnt`` / ``*RlsMtomPrsnt`` instead.
        Returns ``(realized, unrealized, total)`` or ``None`` if not available.
        """
        self._ensure()
        r = self._api.limits(segment="ALL", exchange="ALL", product="ALL")
        if not isinstance(r, dict):
            return None
        if r.get("Error") or r.get("error") or r.get("Error Message"):
            logger.debug("Kotak limits MTM: error payload")
            return None
        r = self._unwrap_limits_root(r)
        mtm_keys = (
            "UnrealizedMtomPrsnt",
            "RealizedMtomPrsnt",
            "CashUnRlsMtomPrsnt",
            "FoUnRlsMtomPrsnt",
            "ComUnRlsMtomPrsnt",
            "CurUnRlsMtomPrsnt",
            "CashRlsMtomPrsnt",
            "FoRlsMtomPrsnt",
            "ComRlsMtomPrsnt",
            "CurRlsMtomPrsnt",
        )
        kl = {str(k).lower() for k in r}
        if not any(name.lower() in kl for name in mtm_keys):
            logger.info(
                "Kotak limits: no RMS MTM keys in payload (portfolio MTM falls back to positions); sample keys: %s",
                sorted(kl)[:35],
            )
            return None

        unreal_agg = self._limits_get_float(
            r, "UnrealizedMtomPrsnt", "UnrealizedMtom", "unrealizedMtomPrsnt"
        )
        real_agg = self._limits_get_float(r, "RealizedMtomPrsnt", "RealizedMtom", "realizedMtomPrsnt")
        unreal_seg = (
            self._limits_get_float(r, "CashUnRlsMtomPrsnt")
            + self._limits_get_float(r, "FoUnRlsMtomPrsnt")
            + self._limits_get_float(r, "ComUnRlsMtomPrsnt")
            + self._limits_get_float(r, "CurUnRlsMtomPrsnt")
        )
        real_seg = (
            self._limits_get_float(r, "CashRlsMtomPrsnt")
            + self._limits_get_float(r, "FoRlsMtomPrsnt")
            + self._limits_get_float(r, "ComRlsMtomPrsnt")
            + self._limits_get_float(r, "CurRlsMtomPrsnt")
        )
        if unreal_agg != 0.0 or real_agg != 0.0:
            unreal, realized = unreal_agg, real_agg
        else:
            unreal, realized = unreal_seg, real_seg
        total = realized + unreal
        return (realized, unreal, total)

    def _kotak_segment_from_xts(self, exchange_segment: int) -> Optional[str]:
        return XTS_EXCHANGE_SEGMENT_TO_KOTAK.get(int(exchange_segment))

    def get_ltp_map(self, instruments: List[dict]) -> Dict[int, float]:
        self._ensure()
        if not instruments:
            return {}
        batch: List[dict] = []
        for ins in instruments:
            xs = ins.get("exchangeSegment")
            iid = ins.get("exchangeInstrumentID")
            if xs is None or iid is None:
                continue
            seg = self._kotak_segment_from_xts(int(xs))
            if not seg:
                continue
            batch.append({"instrument_token": str(int(iid)), "exchange_segment": seg})
        if not batch:
            return {}
        r = self._quotes_get(batch, "ltp")
        return self._parse_ltp_response(r, [int(b["instrument_token"]) for b in batch])

    def _parse_ltp_response(self, r: Any, tokens: List[int]) -> Dict[int, float]:
        out: Dict[int, float] = {}
        if r is None:
            return out
        if isinstance(r, dict):
            if r.get("Error") or r.get("error"):
                logger.debug("Kotak quotes error: %s", r.get("Error") or r.get("error"))
                return out
            if r.get("fault"):
                return out

        def try_row(row: dict) -> None:
            tk = row.get("tk") or row.get("instrument_token") or row.get("instrumentToken")
            ltp = (
                row.get("ltp")
                or row.get("last_traded_price")
                or row.get("iv")
                or row.get("Ltp")
            )
            if ltp is None:
                return
            tid = None
            if tk is not None:
                try:
                    tid = int(tk)
                except (TypeError, ValueError):
                    return
            elif len(tokens) == 1:
                tid = tokens[0]
            if tid is None:
                return
            try:
                out[tid] = float(ltp)
            except (TypeError, ValueError):
                pass

        for row in self._quote_rows_from_response(r):
            try_row(row)

        # Fallback: JSON walk
        if not out:
            self._walk_json_for_ltp(r, out)

        # Ensure requested tokens present
        for t in tokens:
            if t not in out and str(t) not in str(r):
                continue
        return out

    def _walk_json_for_ltp(self, obj: Any, acc: Dict[int, float]) -> None:
        if isinstance(obj, dict):
            tk = obj.get("tk")
            ltp = obj.get("ltp") or obj.get("last_traded_price") or obj.get("iv")
            if tk is not None and ltp is not None:
                try:
                    acc[int(tk)] = float(ltp)
                except (TypeError, ValueError):
                    pass
            for v in obj.values():
                self._walk_json_for_ltp(v, acc)
        elif isinstance(obj, list):
            for v in obj:
                self._walk_json_for_ltp(v, acc)

    @staticmethod
    def _collect_first_quote_price(obj: Any, acc: List[float], keys: Tuple[str, ...]) -> None:
        if acc:
            return
        if isinstance(obj, dict):
            for k in keys:
                v = obj.get(k)
                if v is not None and str(v).strip() != "":
                    try:
                        acc.append(float(v))
                        return
                    except (TypeError, ValueError):
                        pass
            for v in obj.values():
                KotakNeoClient._collect_first_quote_price(v, acc, keys)
        elif isinstance(obj, list):
            for v in obj:
                KotakNeoClient._collect_first_quote_price(v, acc, keys)

    @staticmethod
    def _flatten_quote_data_rows(data: Any) -> List[dict]:
        rows: List[dict] = []
        if isinstance(data, list):
            for x in data:
                if isinstance(x, dict):
                    rows.append(x)
        elif isinstance(data, dict):
            for v in data.values():
                if isinstance(v, dict):
                    rows.append(v)
                elif isinstance(v, list):
                    for x in v:
                        if isinstance(x, dict):
                            rows.append(x)
        return rows

    @classmethod
    def _quote_rows_from_response(cls, r: Any) -> List[dict]:
        """Quote rows: wrapped ``data`` / ``message`` / ``result``, or a bare JSON list (e22 gateway)."""
        rows: List[dict] = []
        if isinstance(r, list):
            rows.extend(cls._flatten_quote_data_rows(r))
            return rows
        if not isinstance(r, dict):
            return rows
        for key in ("data", "message", "result"):
            part = r.get(key)
            rows.extend(cls._flatten_quote_data_rows(part))
        return rows

    @staticmethod
    def _quote_row_first_price(row: dict) -> Optional[float]:
        for k in (
            "ltp",
            "iv",
            "last_traded_price",
            "Ltp",
            "close",
            "lp",
            "openingPrice",
            "buy_price",
            "sell_price",
        ):
            v = row.get(k)
            if v is not None and str(v).strip() != "":
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        oh = row.get("ohlc")
        if isinstance(oh, dict):
            # Kotak index: nested ``close`` is often *prior* settlement (outside today H/L), not live LTP.
            for k in ("ltp", "open", "high", "low", "close"):
                v = oh.get(k)
                if v is None or str(v).strip() == "":
                    continue
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if k == "close" and oh.get("low") is not None and oh.get("high") is not None:
                    try:
                        lo, hi = sorted((float(oh["low"]), float(oh["high"])))
                        if fv < lo - 1e-6 or fv > hi + 1e-6:
                            continue
                    except (TypeError, ValueError):
                        pass
                return fv
        return None

    def _parse_index_ltp_from_quotes(self, r: Any) -> Optional[float]:
        if r is None:
            return None
        if isinstance(r, dict):
            if r.get("fault"):
                return None
            if r.get("Error") or r.get("error") or r.get("Error Message"):
                return None
        for row in self._quote_rows_from_response(r):
            px = self._quote_row_first_price(row)
            if px is not None:
                return px
        acc: List[float] = []
        # Omit top-level ``close`` in walk — can match nested prior-day index settlement in ``ohlc``.
        self._collect_first_quote_price(
            r, acc, ("iv", "ltp", "last_traded_price", "Ltp", "lp", "buy_price")
        )
        return acc[0] if acc else None

    def _index_quote_token_variants(self, index_name: str) -> List[Tuple[str, str]]:
        """(instrument_token, exchange_segment) pairs for getQuote ``neosymbol``.

        Kotak ``Quotes.md`` uses display names on cash segment, e.g.
        ``{"instrument_token": "Nifty 50", "exchange_segment": "nse_cm"}``.
        Some gateways reject ``nse_cm|26000`` / ``nse_cm|NIFTY`` with *Invalid neosymbol*; try those after
        names. ``INDEX`` segment (see webSocket docs) is tried for each token form.
        """
        meta = KOTAK_INDEX_META.get(index_name)
        if not meta:
            return []
        seg = str(meta["spot_seg"])
        primary = str(meta["index_quote_name"])
        names = [primary] + [x for x in KOTAK_INDEX_QUOTE_FALLBACKS.get(index_name, []) if x != primary]
        seen: set = set()
        out: List[Tuple[str, str]] = []

        def add(tok: str, exchange: str) -> None:
            t = str(tok).strip()
            s = str(exchange).strip()
            if not t:
                return
            k = (t, s)
            if k in seen:
                return
            seen.add(k)
            out.append(k)

        for n in names:
            add(n, seg)
        for n in names:
            add(n, "INDEX")

        raw_tok = meta.get("spot_quote_token")
        if raw_tok is not None:
            try:
                t_i = int(raw_tok)
                if self._valid_spot_index_token(t_i):
                    for s in (seg, "INDEX"):
                        add(str(t_i), s)
            except (TypeError, ValueError):
                pass

        sym = meta.get("spot_quote_symbol")
        if sym:
            for s in (seg, "INDEX"):
                add(str(sym).strip(), s)

        if index_name == "SENSEX":
            sn = self._sensex_numeric_spot_token()
            if sn and self._valid_spot_index_token(sn):
                for s in ("bse_cm", "INDEX"):
                    add(str(sn), s)

        return out

    def _record_index_ltp_tick(self, index_name: str, ltp: float) -> None:
        ts = int(time.time())
        lst = self._index_ltp_ticks[index_name]
        lst.append((ts, float(ltp)))
        while len(lst) > self._ltp_history_cap:
            lst.pop(0)

    def get_spot_ltp(self, index_config: IndexConfig) -> Optional[float]:
        if index_config.name not in KOTAK_INDEX_META:
            return None
        self._ensure()
        for token, seg in self._index_quote_token_variants(index_config.name):
            inst = [{"instrument_token": token, "exchange_segment": seg}]
            ix_order = (False, True) if str(token).strip().isdigit() else (True, False)
            for qt in ("ltp", "all", "ohlc"):
                for is_ix in ix_order:
                    r = self._quotes_get(inst, qt, is_index=is_ix)
                    val = self._parse_index_ltp_from_quotes(r)
                    if val is not None:
                        self._record_index_ltp_tick(index_config.name, val)
                        return val
        variants = self._index_quote_token_variants(index_config.name)
        tried = ", ".join(f"{t}|{s}" for t, s in variants[:8])
        if len(variants) > 8:
            tried += f", ...({len(variants)} total)"
        logger.warning(
            "Kotak: index quote returned no LTP for %s (tried %s); run scripts/debug_kotak_quotes.py",
            index_config.name,
            tried or "(no variants)",
        )
        return None

    def get_option_ltp(self, index_config: IndexConfig, instrument_id: int) -> Optional[float]:
        if index_config.name not in KOTAK_INDEX_META:
            return None
        xs = 2 if index_config.name == "NIFTY" else 12
        m = self.get_ltp_map(
            [{"exchangeSegment": xs, "exchangeInstrumentID": int(instrument_id)}]
        )
        ltp = m.get(int(instrument_id))
        if ltp is None:
            return None
        try:
            return float(ltp)
        except (TypeError, ValueError):
            return None

    def get_spot_ohlc_bars(
        self,
        index_config: IndexConfig,
        start: datetime.datetime,
        end: datetime.datetime,
        compression_seconds: int = 60,
    ) -> List[Dict[str, Any]]:
        """
        Best-effort intraday OHLC via Kotak ``quotes`` (``ohlc``).

        If the API shape differs from XTS 1-minute series, returns [] (calm zone degrades).
        """
        meta = KOTAK_INDEX_META.get(index_config.name)
        if not meta:
            return []
        self._ensure()
        bars: List[Dict[str, Any]] = []
        for token, seg in self._index_quote_token_variants(index_config.name):
            inst = [{"instrument_token": token, "exchange_segment": seg}]
            ix_order = (False, True) if str(token).strip().isdigit() else (True, False)
            for qt in ("all", "ohlc"):
                for is_ix in ix_order:
                    r = self._quotes_get(inst, qt, is_index=is_ix)
                    bars = self._coerce_ohlc_bars(r, compression_seconds)
                    if not bars:
                        bars = self._bars_from_quote_snapshot_ohlc(r)
                    if bars:
                        break
                if bars:
                    break
            if bars:
                break
        if not bars:
            ltp = self.get_spot_ltp(index_config)
            bars = self._bars_from_ltp_history(index_config.name, start, end, ltp)
        if not bars:
            logger.warning(
                "Kotak OHLC: no bars for %s (quotes + LTP history empty); volatility DB will not update",
                index_config.name,
            )
            return []
        ist = pytz.timezone("Asia/Kolkata")
        s = start if start.tzinfo else ist.localize(start)
        e = end if end.tzinfo else ist.localize(end)
        start_u = int(s.timestamp())
        end_u = int(e.timestamp())
        out = [b for b in bars if start_u <= int(b["bar_unix"]) <= end_u]
        return [{**b, "bar_unix": _spot_bar_unix_for_db(int(b["bar_unix"]))} for b in out]

    @staticmethod
    def _is_invalid_snapshot_ohlc(o: float, h: float, l: float, c: float) -> bool:
        """Reject obvious non-1m snapshot rows (e.g., prior close outside today's H/L)."""
        lo, hi = sorted((float(l), float(h)))
        fc = float(c)
        return fc < lo - 1e-6 or fc > hi + 1e-6

    def _coerce_ohlc_bars(self, r: Any, compression_seconds: int) -> List[Dict[str, Any]]:
        if r is None:
            return []
        if isinstance(r, dict) and r.get("fault"):
            return []
        candidates: List[Any] = []
        if isinstance(r, list):
            candidates.extend(r)
        elif isinstance(r, dict):
            for key in ("data", "message", "ohlc", "candles", "result"):
                v = r.get(key)
                if isinstance(v, list):
                    candidates.extend(v)
                elif isinstance(v, dict):
                    for vv in v.values():
                        if isinstance(vv, list):
                            candidates.extend(vv)
        else:
            return []
        out: List[Dict[str, Any]] = []
        for row in candidates:
            if not isinstance(row, dict):
                continue
            ts = (
                row.get("bar_unix")
                or row.get("time")
                or row.get("t")
                or row.get("unix")
                or row.get("et")
            )
            o = row.get("open") or row.get("o") or row.get("openingPrice")
            h = row.get("high") or row.get("h") or row.get("highPrice")
            l = row.get("low") or row.get("l") or row.get("lowPrice")
            c = row.get("close") or row.get("c") or row.get("ltp") or row.get("iv")
            vol = row.get("volume") or row.get("v")
            try:
                if ts is None or o is None or h is None or l is None or c is None:
                    continue
                fo = float(o)
                fh = float(h)
                fl = float(l)
                fc = float(c)
                if self._is_invalid_snapshot_ohlc(fo, fh, fl, fc):
                    continue
                tu = int(float(ts))
                if tu > 1_000_000_000_000:  # ms
                    tu //= 1000
                out.append(
                    {
                        "bar_unix": tu,
                        "open": fo,
                        "high": fh,
                        "low": fl,
                        "close": fc,
                        "volume": float(vol) if vol is not None else None,
                    }
                )
            except (TypeError, ValueError):
                continue
        out.sort(key=lambda b: b["bar_unix"])
        return out

    def _bars_from_quote_snapshot_ohlc(self, r: Any) -> List[Dict[str, Any]]:
        """Single-row REST quote with nested ``ohlc`` or flat O/H/L/C (no time series)."""
        if r is None:
            return []
        if isinstance(r, dict) and r.get("fault"):
            return []
        rows = self._quote_rows_from_response(r)
        if not rows:
            return []
        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.datetime.now(ist).replace(second=0, microsecond=0)
        default_tu = int(now.timestamp())
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            # Broker "all"/"ohlc" for indices is usually a snapshot where nested OHLC is session/day
            # values, not 1-minute candles. For synthetic 1m bars, use live LTP/IV only.
            live_px = row.get("ltp") or row.get("iv")
            oh = row.get("ohlc")
            if live_px is None and isinstance(oh, dict):
                live_px = oh.get("ltp")
            if live_px is None:
                continue
            ts = (
                row.get("bar_unix")
                or row.get("time")
                or row.get("t")
                or row.get("boeSec")
                or row.get("uSec")
            )
            try:
                fp = float(live_px)
                tu = default_tu if ts is None else int(float(ts))
                if tu > 1_000_000_000_000:
                    tu //= 1000
                tu = tu - (tu % 60)
                out.append(
                    {
                        "bar_unix": tu,
                        "open": fp,
                        "high": fp,
                        "low": fp,
                        "close": fp,
                        "volume": None,
                    }
                )
            except (TypeError, ValueError):
                continue
        out.sort(key=lambda b: b["bar_unix"])
        return out

    def _bars_from_ltp_history(
        self,
        index_name: str,
        start: datetime.datetime,
        end: datetime.datetime,
        last_ltp: Optional[float],
    ) -> List[Dict[str, Any]]:
        ist = pytz.timezone("Asia/Kolkata")

        def _to_epoch_local(dt: datetime.datetime) -> int:
            d = dt if dt.tzinfo else ist.localize(dt)
            return int(d.astimezone(ist).timestamp())

        su, eu = _to_epoch_local(start), _to_epoch_local(end)
        ticks = [(t, p) for t, p in self._index_ltp_ticks.get(index_name, []) if su <= t <= eu]
        if not ticks and last_ltp is not None:
            ticks = [(eu, float(last_ltp))]
        buckets: Dict[int, List[float]] = {}
        for ts, px in ticks:
            m = ts - (ts % 60)
            buckets.setdefault(m, []).append(float(px))
        bars: List[Dict[str, Any]] = []
        for m in sorted(buckets.keys()):
            vals = buckets[m]
            bars.append(
                {
                    "bar_unix": m,
                    "open": vals[0],
                    "high": max(vals),
                    "low": min(vals),
                    "close": vals[-1],
                    "volume": None,
                }
            )
        return bars

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
        from xts_client import marketable_limit_price

        self._ensure()
        meta = KOTAK_INDEX_META.get(index_config.name)
        if not meta:
            return None
        tm = self._token_meta.get(int(instrument_id))
        if not tm:
            logger.warning("Kotak place_market_order: missing token cache for %s", instrument_id)
            return None
        s = MARKETABLE_LIMIT_SLIPPAGE_PCT if slippage_pct is None else slippage_pct
        ltp_val = ltp if ltp is not None else self.get_option_ltp(index_config, instrument_id)
        if ltp_val is None:
            logger.warning("Kotak: no LTP for %s", instrument_id)
            return None
        limit_price = marketable_limit_price(
            float(ltp_val),
            order_side,
            s,
            float(index_config.tick_size),
        )
        tt = "B" if (order_side or "").strip().upper() == "BUY" else "S"
        r = self._api.place_order(
            exchange_segment=meta["fo_seg"],
            product=product_type,
            price=str(round(float(limit_price), 2)),
            order_type="L",
            quantity=str(int(quantity)),
            validity="DAY",
            trading_symbol=tm["trdSym"],
            transaction_type=tt,
            amo="NO",
            trigger_price="0",
            tag=(tag or None),
            scrip_token=str(int(instrument_id)),
        )
        oid = parse_kotak_place_order_n_ord_no(r)
        return oid

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
        self._ensure()
        meta = KOTAK_INDEX_META.get(index_config.name)
        if not meta:
            return None
        tm = self._token_meta.get(int(instrument_id))
        if not tm:
            logger.warning("Kotak place_sl_order: missing token cache for %s", instrument_id)
            return None
        tt = "B" if (order_side or "").strip().upper() == "BUY" else "S"
        r = self._api.place_order(
            exchange_segment=meta["fo_seg"],
            product=product_type,
            price=str(round(float(limit_price), 2)),
            order_type="SL",
            quantity=str(int(quantity)),
            validity="DAY",
            trading_symbol=tm["trdSym"],
            transaction_type=tt,
            amo="NO",
            trigger_price=str(round(float(stop_price), 2)),
            tag=(tag or None),
            scrip_token=str(int(instrument_id)),
        )
        return parse_kotak_place_order_n_ord_no(r)

    def cancel_order(self, app_order_id: int, tag: str) -> None:
        self._ensure()
        self._api.cancel_order(str(app_order_id), amo="NO", isVerify=False)

    def cancel_all_orders(self, index_config: IndexConfig, instrument_id: int) -> None:
        self._ensure()
        book = self._api.order_report()
        data = book.get("data") if isinstance(book, dict) else None
        if not isinstance(data, list):
            return
        for o in data:
            try:
                if int(o.get("tok") or 0) != int(instrument_id):
                    continue
            except (TypeError, ValueError):
                continue
            st = (o.get("ordSt") or o.get("stat") or "").lower()
            if st in ("traded", "complete", "rejected", "cancelled"):
                continue
            n = o.get("nOrdNo")
            if n:
                self._api.cancel_order(str(n), amo="NO", isVerify=False)

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
        client_id: Optional[str] = None,
    ) -> Any:
        del client_id  # XTS-only; Kotak ignores
        self._ensure()
        kot_pt = _map_xts_order_type_to_kotak_pt(order_type)
        tp = str(round(float(stop_price), 2)) if float(stop_price or 0) > 0 else "0"
        mod = ModifyOrder(self._api.api_client)
        return mod.modification_with_orderid(
            order_id=str(app_order_id),
            price=str(round(float(limit_price), 2)),
            order_type=kot_pt,
            quantity=str(int(quantity)),
            validity=str(time_in_force or "DAY"),
            instrument_token=None,
            exchange_segment=None,
            product=product_type,
            trading_symbol=None,
            transaction_type=None,
            trigger_price=tp,
            dd="NA",
            market_protection="0",
            disclosed_quantity=str(int(disclosed_quantity or 0)),
            filled_quantity="0",
            amo="NO",
        )

    @staticmethod
    def parse_ohlc_data_response(data_response: str) -> List[Dict[str, Any]]:
        """Compatibility shim: delegate to XTS parser (unused on Kotak path)."""
        from xts_client import XTSClient

        return XTSClient.parse_ohlc_data_response(data_response)
