import os
import time

from flask import Flask, jsonify, render_template_string, request
from flask_basicauth import BasicAuth
import sqlite3

from state import (
    get_snapshot,
    get_all_trading_flags,
    get_mtm_snapshots_enabled,
    set_mtm_snapshots_enabled,
    set_trading_flag,
)

from bot_log_filter import bot_log_line_is_survivor_event
from db import fetch_spot_market_rows

try:
    from config import CALM_ZONE_BAR_UNIX_OFFSET_SEC, LEG_TARGET_PCT
except ImportError:
    LEG_TARGET_PCT = 65.0
    CALM_ZONE_BAR_UNIX_OFFSET_SEC = 0


def _default_bot_log_path() -> str:
    """Same default as bot.py: project directory, not process cwd."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.log")


def _bot_log_abs_path() -> str:
    env_lp = os.environ.get("BOT_LOG_PATH")
    return os.path.abspath(env_lp) if env_lp else _default_bot_log_path()


def silence_werkzeug_http_access_log() -> None:
    """
    Stop Flask/Werkzeug dev server from logging each request (GET ... HTTP/1.1) to Python logging.

    Werkzeug's _internal._log() may add handlers back to the 'werkzeug' logger; muting loggers alone
    is not reliable. Disabling log_request prevents access lines from being emitted at all.
    """
    try:
        from werkzeug.serving import WSGIRequestHandler

        def _silent_log_request(self, code="-", size="-") -> None:  # noqa: ANN001
            return

        WSGIRequestHandler.log_request = _silent_log_request  # type: ignore[assignment]
    except Exception:
        pass


def _muzzle_werkzeug_and_flask_loggers() -> None:
    """Ensure HTTP/access noise does not propagate to root or bot file handlers."""
    import logging as _logging

    for _name in (
        "werkzeug",
        "werkzeug.serving",
        "werkzeug._internal",
        "flask",
        "flask.app",
    ):
        _lg = _logging.getLogger(_name)
        _lg.handlers.clear()
        _lg.propagate = False
        _lg.setLevel(_logging.ERROR)


def ensure_http_access_not_logged() -> None:
    """Call from create_app and again right before app.run() (Werkzeug may re-init loggers)."""
    silence_werkzeug_http_access_log()
    _muzzle_werkzeug_and_flask_loggers()


def read_bot_log_tail(
    max_lines: int = 500,
    max_bytes: int = 8_388_608,
    mode: str = "survivor",
) -> dict:
    """
    Read the last chunk of the bot log file (same path as bot.py FileHandler).
    Does not accept arbitrary paths from the client (fixed path only).

    ``max_bytes`` is how much of the *end* of the file to load before taking the last
    ``max_lines`` lines — large enough that typical INFO lines are not cut off before "now".
    """
    path = _bot_log_abs_path()
    if mode not in ("survivor", "all"):
        mode = "survivor"
    out: dict = {
        "path": path,
        "lines": [],
        "truncated": False,
        "missing": False,
        "mode": mode,
        "fetched_at_ms": int(time.time() * 1000),
    }
    if not os.path.isfile(path):
        out["missing"] = True
        return out
    try:
        st = os.stat(path)
        out["file_size_bytes"] = st.st_size
        out["file_mtime_ms"] = int(st.st_mtime * 1000)
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                out["empty_file"] = True
                return out
            out["truncated"] = size > max_bytes
            read_size = min(max_bytes, size)
            f.seek(-read_size, os.SEEK_END)
            chunk = f.read().decode("utf-8", errors="replace")
        lines = chunk.splitlines()
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        out["tail_line_count"] = len(lines)
        if mode == "survivor":
            filtered = [ln for ln in lines if bot_log_line_is_survivor_event(ln)]
            out["lines"] = filtered
            out["filtered_empty"] = len(filtered) == 0 and len(lines) > 0
        else:
            out["lines"] = lines
        return out
    except OSError as e:
        out["error"] = str(e)
        return out


DASHBOARD_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>XTS Bot Database Dashboard</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0f172a; color: #e2e8f0; padding: 20px; }
    .container { max-width: 1400px; margin: 0 auto; }
    h1 { margin-bottom: 20px; color: #f1f5f9; }
    .tabs { display: flex; gap: 10px; margin-bottom: 20px; border-bottom: 2px solid #334155; overflow-x: auto; }
    .tab-btn { padding: 10px 20px; background: #1e293b; border: none; color: #94a3b8; cursor: pointer; font-size: 14px; transition: all 0.3s; white-space: nowrap; }
    .tab-btn:hover { background: #334155; }
    .tab-btn.active { background: #0ea5e9; color: #fff; border-radius: 4px 4px 0 0; }
    .tab-content { display: none; }
    .tab-content.active { display: block; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 15px; margin-bottom: 20px; }
    .card { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 15px; }
    .card-title { font-weight: bold; margin-bottom: 10px; color: #cbd5e1; font-size: 12px; text-transform: uppercase; }
    .metric { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #334155; }
    .metric:last-child { border-bottom: none; }
    .metric-label { color: #94a3b8; font-size: 13px; }
    .metric-value { font-weight: bold; color: #f1f5f9; }
    .positive { color: #10b981; }
    .negative { color: #ef4444; }
    table { width: 100%; border-collapse: collapse; background: #1e293b; border: 1px solid #334155; border-radius: 6px; overflow: hidden; margin-bottom: 20px; }
    th { background: #0f172a; padding: 12px; text-align: left; font-size: 12px; font-weight: bold; color: #cbd5e1; text-transform: uppercase; border-bottom: 1px solid #334155; }
    td { padding: 10px 12px; border-bottom: 1px solid #334155; font-size: 13px; }
    tr:hover { background: #334155; }
    .status-open { background: #065f46; color: #d1fae5; padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }
    .status-closed { background: #7c2d12; color: #fed7aa; padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }
    .status-disabled { background: #92400e; color: #fef3c7; padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }
    .disabled-banner { background: #92400e; color: #fef3c7; padding: 14px 20px; border-radius: 6px; margin-bottom: 16px; font-size: 15px; font-weight: bold; text-align: center; border: 2px solid #d97706; }
    .setting-row { display: flex; align-items: center; gap: 8px; cursor: pointer; }
    .setting-row input[type="checkbox"] { cursor: pointer; }
    .tag { background: #1e40af; color: #dbeafe; padding: 2px 6px; border-radius: 3px; font-size: 11px; }
    .meta-info { background: #0f172a; padding: 15px; border-radius: 8px; margin-bottom: 20px; display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; }
    .meta-item { border-right: 1px solid #334155; }
    .meta-item:last-child { border-right: none; }
    .meta-label { color: #94a3b8; font-size: 12px; }
    .meta-value { font-size: 18px; font-weight: bold; margin-top: 5px; }
    .subtext { color: #94a3b8; font-size: 12px; margin-top: 4px; line-height: 1.35; }
    .warn-box { background: #7c2d12; color: #fed7aa; padding: 10px 12px; border-radius: 6px; border: 1px solid #fb923c; font-size: 12px; line-height: 1.4; }
    .refresh-time { color: #64748b; font-size: 11px; margin-top: 20px; text-align: center; }
    .log-view { white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, Consolas, monospace; font-size: 12px; line-height: 1.45; max-height: 70vh; overflow: auto; background: #0f172a; padding: 14px; border: 1px solid #334155; border-radius: 6px; color: #e2e8f0; }
    tr.row-calm td { background: rgba(16, 185, 129, 0.22) !important; }
    #volatility-chart { width: 100%; height: 420px; min-height: 280px; }
  </style>
  <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script>
</head>
<body>
  <div class="container">
    <h1>📊 XTS Bot Database Dashboard</h1>
    
    <div id="dash-disabled-banner"></div>
    <div class="meta-info" id="meta-info">Loading...</div>
    
    <div class="tabs">
      <button class="tab-btn active" onclick="switchTab(event, 'overview')">Overview</button>
      <button class="tab-btn" onclick="switchTab(event, 'strategies')">Strategies</button>
      <button class="tab-btn" onclick="switchTab(event, 'positions')">Positions</button>
      <button class="tab-btn" onclick="switchTab(event, 'orders')">Orders</button>
      <button class="tab-btn" onclick="switchTab(event, 'trades')">Closed Trades</button>
      <button class="tab-btn" onclick="switchTab(event, 'mtm')">MTM Snapshots</button>
      <button class="tab-btn" onclick="switchTab(event, 'volatility')">Volatility Monitor</button>
      <button class="tab-btn" onclick="switchTab(event, 'botlog')">Bot log</button>
    </div>

    <div id="overview" class="tab-content active">
      <div class="grid">
        <div class="card">
          <div class="card-title">Portfolio Status</div>
          <div id="portfolio-status">Loading...</div>
        </div>
        <div class="card">
          <div class="card-title">Today's Summary</div>
          <div id="today-summary">Loading...</div>
        </div>
        <div class="card">
          <div class="card-title">Margin Gate</div>
          <div id="margin-gate-card">Loading...</div>
        </div>
        <div class="card">
          <div class="card-title">Survivor SL → cost</div>
          <div id="survivor-cost-card" class="meta-label">Loading...</div>
        </div>
        <div class="card">
          <div class="card-title">Settings</div>
          <div id="settings-card">
            <label class="setting-row">
              <input type="checkbox" id="mtm-snapshots-toggle" />
              <span>Enable MTM snapshots</span>
            </label>
            <div class="meta-label" style="margin-top: 6px; font-size: 11px;">Writes strategy MTM to DB every ~60s when enabled. Turn on to populate the MTM Snapshots tab.</div>
          </div>
        </div>
        <div class="card">
          <div class="card-title">Trading flags</div>
          <p class="meta-label" style="font-size: 11px; margin-bottom: 10px;">Changes require login to apply.</p>
          <div id="dash-flags-card">
            <label class="setting-row"><input type="checkbox" id="dash-flag-premium" /> <span>Premium-based strike</span></label>
            <label class="setting-row"><input type="checkbox" id="dash-flag-strategy-sl" /> <span>Strategy SL enabled</span></label>
            <label class="setting-row"><input type="checkbox" id="dash-flag-non-expiry" /> <span>Trade non-expiry day</span></label>
            <label class="setting-row"><input type="checkbox" id="dash-flag-mtm" /> <span>MTM snapshots</span></label>
          </div>
          <button type="button" id="dash-flags-save" class="tab-btn" style="margin-top: 10px; padding: 6px 12px;">Save (requires login)</button>
        </div>
      </div>
      <div id="dash-auth-modal" style="display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 1000; align-items: center; justify-content: center;">
        <div style="background: #1e293b; padding: 24px; border-radius: 8px; max-width: 320px; width: 90%; border: 1px solid #334155;">
          <p style="margin: 0 0 12px 0; font-weight: bold; color: #e2e8f0;">Confirm: enter username and password</p>
          <input type="text" id="dash-auth-user" placeholder="Username" style="width: 100%; padding: 8px; margin-bottom: 8px; box-sizing: border-box; background: #0f172a; border: 1px solid #334155; color: #e2e8f0;" />
          <input type="password" id="dash-auth-pass" placeholder="Password" style="width: 100%; padding: 8px; margin-bottom: 12px; box-sizing: border-box; background: #0f172a; border: 1px solid #334155; color: #e2e8f0;" />
          <div style="display: flex; gap: 8px;">
            <button type="button" id="dash-auth-confirm" style="padding: 8px 16px; background: #0ea5e9; color: white; border: none; border-radius: 4px; cursor: pointer;">Apply</button>
            <button type="button" id="dash-auth-cancel" style="padding: 8px 16px; background: #64748b; color: white; border: none; border-radius: 4px; cursor: pointer;">Cancel</button>
          </div>
        </div>
      </div>
      <div class="card" style="margin-bottom: 15px;">
        <div class="card-title">Scheduler health</div>
        <p class="meta-label" style="margin-bottom: 8px;" id="scheduler-notes"></p>
        <div id="scheduler-card">Loading...</div>
      </div>
      <table>
        <thead>
          <tr><th>Strategy</th><th>Status</th><th>Strike</th><th>Entry Time</th><th>Positions</th><th>Margin / Hedge</th></tr>
        </thead>
        <tbody id="overview-rows">Loading...</tbody>
      </table>
    </div>

    <div id="strategies" class="tab-content">
      <table>
        <thead>
          <tr><th>Strategy Name</th><th>Execution Date</th><th>Strike</th><th>Entry Time</th><th>Status</th><th>Lots</th><th>Leg SL %</th><th>Strategy SL</th><th>Margin Req</th><th>Message</th><th>Hedge</th></tr>
        </thead>
        <tbody id="strategies-rows">Loading...</tbody>
      </table>
    </div>

    <div id="positions" class="tab-content">
      <table>
        <thead>
          <tr><th>Strategy</th><th>Symbol</th><th>Quantity</th><th>Entry Price</th><th>Target (60%)</th><th>Exit Price</th><th>Entry Time</th><th>Exit Time</th><th>Status</th></tr>
        </thead>
        <tbody id="positions-rows">Loading...</tbody>
      </table>
    </div>

    <div id="orders" class="tab-content">
      <table>
        <thead>
          <tr><th>Strategy</th><th>Order Tag</th><th>Symbol</th><th>Quantity</th><th>Type</th><th>Side</th><th>Status</th><th>Traded Price</th></tr>
        </thead>
        <tbody id="orders-rows">Loading...</tbody>
      </table>
    </div>

    <div id="trades" class="tab-content">
      <table>
        <thead>
          <tr><th>Strategy</th><th>Execution Date</th><th>Strike</th><th>Entry Time</th><th>Exit Time</th><th>Realized P&L</th><th>Final MTM</th><th>Reason</th></tr>
        </thead>
        <tbody id="trades-rows">Loading...</tbody>
      </table>
    </div>

    <div id="mtm" class="tab-content">
      <div class="card" style="margin-bottom: 15px;">
        <label class="setting-row">
          <input type="checkbox" id="mtm-snapshots-toggle-tab" />
          <span>Enable MTM snapshots</span>
        </label>
        <div class="meta-label" style="margin-top: 6px; font-size: 11px;">When enabled, strategy MTM is written to the database about every 60 seconds (table below).</div>
      </div>
      <table>
        <thead>
          <tr><th>Strategy</th><th>Total MTM</th><th>Realized</th><th>Unrealized</th><th>Timestamp</th></tr>
        </thead>
        <tbody id="mtm-rows">Loading...</tbody>
      </table>
    </div>

    <div id="volatility" class="tab-content">
      <div class="card" style="margin-bottom: 12px;">
        <div class="card-title">Calm zone — <span id="volatility-index-label">—</span></div>
        <p class="meta-label" style="margin-bottom: 8px;">1-minute spot OHLC from the database; Range/Ratio use a 5-minute sliding window. Green rows: calm zone (NIFTY range &lt; 50, non-NIFTY indices &lt; 120, body/range &lt; 0.25). Timezone is IST. Index follows the bot&rsquo;s active index from <code>/state</code>.</p>
        <label class="setting-row" style="font-size: 12px; color: #cbd5e1;">
          <span>Chart mode:</span>
          <select id="volatility-chart-mode" style="background: #0f172a; color: #e2e8f0; border: 1px solid #334155; padding: 4px 6px;">
            <option value="candles">Candles</option>
            <option value="line">Line</option>
            <option value="both">Candles + Line</option>
          </select>
        </label>
      </div>
      <table>
        <thead>
          <tr><th>Time (IST)</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Range</th><th>Ratio</th><th>Status</th></tr>
        </thead>
        <tbody id="volatility-rows">Loading...</tbody>
      </table>
      <div class="card" style="margin-top: 16px;">
        <div class="card-title">1m candlestick + calm zones</div>
        <div id="volatility-chart"></div>
      </div>
    </div>

    <div id="botlog" class="tab-content">
      <div class="card" style="margin-bottom: 12px;">
        <div class="card-title">Bot log file</div>
        <p class="meta-label" style="margin-bottom: 8px;">Tail of <span id="bot-log-path">—</span> (default file is <strong>bot.log next to bot.py/ui.py</strong>, not cwd — override with env <code>BOT_LOG_PATH</code>). Default view shows only <strong>survivor SL-to-cost</strong> lines. Use the checkbox for the full tail. If timestamps never update, the UI process is reading a different file than the bot.</p>
        <label class="setting-row" style="margin-bottom: 8px;">
          <input type="checkbox" id="bot-log-show-full" />
          <span>Show full log (entire tail, not survivor-only)</span>
        </label>
        <button type="button" class="tab-btn" id="bot-log-refresh" style="padding: 6px 12px;">Refresh now</button>
        <p class="meta-label" id="bot-log-meta" style="margin-top: 8px; font-size: 11px;">—</p>
      </div>
      <pre id="bot-log-pre" class="log-view">Loading...</pre>
    </div>

    <div class="refresh-time">Last updated: <span id="update-time">-</span> (Auto-refresh every 2 seconds)</div>
  </div>

  <script>
    function fmt(num) {
      if (num === null || num === undefined) return "-";
      const n = Number(num);
      return n.toFixed(2);
    }

    function getClass(num) {
      if (num === null || num === undefined) return '';
      return num < 0 ? 'negative' : 'positive';
    }

    function fmtMargin(n) {
      if (n === null || n === undefined || isNaN(Number(n))) return '-';
      return `${(Number(n) / 100000).toFixed(2)}L`;
    }

    function fmtIso(iso) {
      if (!iso) return '-';
      try { return new Date(iso).toLocaleString(); } catch (e) { return iso; }
    }

    function escHtml(s) {
      if (s === null || s === undefined) return '';
      return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    function calcRequiredMargin(lots, isExpiry) {
      const perLot = isExpiry ? 315000 : 250000;
      const buffer = 300000;
      return (Number(lots) || 0) * perLot + buffer;
    }

    function switchTab(event, tabName) {
      document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.getElementById(tabName).classList.add('active');
      event.target.classList.add('active');
      if (tabName === 'volatility' && window.__volatilityChart) {
        setTimeout(function() { try { window.__volatilityChart.resize(); } catch (e) {} }, 80);
      }
    }

    function buildCalmMarkAreas(rowsAsc) {
      const areas = [];
      let start = null;
      for (let i = 0; i < rowsAsc.length; i++) {
        const calm = rowsAsc[i].is_calmzone === 1 || rowsAsc[i].is_calmzone === true;
        if (calm) {
          if (start === null) start = i;
        } else if (start !== null) {
          areas.push([{ xAxis: start }, { xAxis: i - 1 }]);
          start = null;
        }
      }
      if (start !== null) {
        areas.push([{ xAxis: start }, { xAxis: rowsAsc.length - 1 }]);
      }
      return areas;
    }

    function barUnixMs(r, offsetSec) {
      const o = Number(offsetSec) || 0;
      if (r.bar_unix != null && r.bar_unix !== '') {
        const sec = Number(r.bar_unix);
        if (!isNaN(sec)) return (sec + o) * 1000;
      }
      return null;
    }

    function inferBarUnixOffsetSec(rows, configuredOffsetSec) {
      const base = Number(configuredOffsetSec) || 0;
      if (!rows || !rows.length) return base;
      let latest = null;
      for (let i = 0; i < rows.length; i++) {
        if (rows[i].bar_unix != null && rows[i].bar_unix !== '') {
          latest = rows[i];
          break;
        }
      }
      if (!latest) return base;
      const sec = Number(latest.bar_unix);
      if (isNaN(sec)) return base;
      const nowSec = Math.floor(Date.now() / 1000);
      const drift = nowSec - (sec + base);
      // If vendor epoch is clearly shifted (few hours), auto-correct for display.
      if (Math.abs(drift) >= 2 * 3600 && Math.abs(drift) <= 12 * 3600) {
        return base + Math.round(drift / 60) * 60;
      }
      return base;
    }

    function formatVolBarTime(r, offsetSec) {
      const ms = barUnixMs(r, offsetSec);
      if (ms != null && !isNaN(ms)) {
        const d = new Date(ms);
        return d.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true }) + ' IST';
      }
      const raw = String(r.bar_time || '').replace(/\s*IST\s*$/i, '').trim();
      if (!raw) return '-';
      const m = raw.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})/);
      if (m) {
        const d = new Date(m[1] + 'T' + m[2] + '+05:30');
        if (!isNaN(d.getTime())) {
          return d.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true }) + ' IST';
        }
      }
      return raw + ' IST';
    }

    function shortChartTimeLabel(r, offsetSec) {
      const ms = barUnixMs(r, offsetSec);
      if (ms != null && !isNaN(ms)) {
        return new Date(ms).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: true });
      }
      const s = String(r.bar_time || '');
      const parts = s.split(/[ T]/);
      return parts.length > 1 ? parts[1].slice(0, 8) : s.slice(-8);
    }

    function updateVolatilityMonitor(vol) {
      const label = document.getElementById('volatility-index-label');
      const tbody = document.getElementById('volatility-rows');
      const chartEl = document.getElementById('volatility-chart');
      const modeSel = document.getElementById('volatility-chart-mode');
      if (!label || !tbody || !chartEl || !modeSel) return;
      const mode = modeSel.value || 'candles';
      const name = vol && vol.index ? vol.index : 'NIFTY';
      label.textContent = name;
      const rows = (vol && vol.rows) ? vol.rows : [];
      const cfgOff = (vol && vol.bar_unix_offset_sec != null) ? Number(vol.bar_unix_offset_sec) : 0;
      const off = inferBarUnixOffsetSec(rows, cfgOff);
      let html = '';
      for (let i = 0; i < rows.length; i++) {
        const r = rows[i];
        const calm = r.is_calmzone === 1 || r.is_calmzone === true;
        const cls = calm ? 'row-calm' : '';
        const ratio = (r.body_range_ratio != null && r.body_range_ratio !== '') ? Number(r.body_range_ratio).toFixed(4) : '-';
        const rng = (r.range_5m != null && r.range_5m !== '') ? Number(r.range_5m).toFixed(2) : '-';
        const st = calm ? 'Calm' : '—';
        html += '<tr class="' + cls + '"><td>' + escHtml(formatVolBarTime(r, off)) + '</td><td>' + fmt(r.open) + '</td><td>' + fmt(r.high) + '</td><td>' + fmt(r.low) + '</td><td>' + fmt(r.close) + '</td><td>' + rng + '</td><td>' + ratio + '</td><td>' + st + '</td></tr>';
      }
      if (!html) {
        html = '<tr><td colspan="8">No spot data yet (market hours / worker running).</td></tr>';
      }
      tbody.innerHTML = html;

      if (typeof echarts === 'undefined') {
        return;
      }
      const asc = rows.slice().sort(function(a, b) {
        const ma = barUnixMs(a, off);
        const mb = barUnixMs(b, off);
        if (ma != null && mb != null) return ma - mb;
        return String(a.bar_time).localeCompare(String(b.bar_time));
      });
      const cats = asc.map(function(r) { return shortChartTimeLabel(r, off); });
      const candleData = asc.map(function(r) {
        return [Number(r.open), Number(r.close), Number(r.low), Number(r.high)];
      });
      const lineData = asc.map(function(r) { return Number(r.close); });
      const markAreas = buildCalmMarkAreas(asc);
      if (!window.__volatilityChart) {
        window.__volatilityChart = echarts.init(chartEl);
      }
      const ch = window.__volatilityChart;
      const series = [];
      if (mode === 'candles' || mode === 'both') {
        series.push({
          type: 'candlestick',
          name: name + ' candles',
          data: candleData,
          itemStyle: {
            color: '#22c55e',
            color0: '#ef4444',
            borderColor: '#22c55e',
            borderColor0: '#ef4444'
          },
          markArea: {
            silent: true,
            itemStyle: { color: 'rgba(34, 197, 94, 0.22)' },
            data: markAreas
          }
        });
      }
      if (mode === 'line' || mode === 'both') {
        series.push({
          type: 'line',
          name: name + ' close',
          data: lineData,
          smooth: false,
          showSymbol: false,
          lineStyle: { color: '#38bdf8', width: 2 }
        });
      }
      ch.setOption({
        backgroundColor: 'transparent',
        animation: false,
        tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
        grid: { left: '52', right: '20', top: '20', bottom: '56' },
        xAxis: { type: 'category', data: cats, axisLabel: { color: '#94a3b8', rotate: 40, fontSize: 10 } },
        yAxis: { scale: true, axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#334155' } } },
        series: series
      });
      setTimeout(function() { try { ch.resize(); } catch (e) {} }, 60);
    }

    async function loadDashboard() {
      try {
        const showFullLog = document.getElementById('bot-log-show-full')?.checked;
        const logMode = showFullLog ? 'all' : 'survivor';
        const noCache = { cache: 'no-store', credentials: 'same-origin' };
        const [state, strategies, positions, orders, trades, mtm, volmon, botlog] = await Promise.all([
          fetch('/state', noCache).then(r => r.json()),
          fetch('/api/strategies', noCache).then(r => r.json()),
          fetch('/api/positions', noCache).then(r => r.json()),
          fetch('/api/orders', noCache).then(r => r.json()),
          fetch('/api/trades', noCache).then(r => r.json()),
          fetch('/api/mtm', noCache).then(r => r.json()),
          fetch('/api/volatility-monitor', noCache).then(r => r.json()),
          fetch('/api/bot-log?lines=2500&mode=' + encodeURIComponent(logMode) + '&_=' + Date.now(), noCache).then(r => r.json()),
        ]);

        const portfolio = state.portfolio || {};
        const idx = state.index || {};
        const stateStrategies = state.strategies || {};

        const s = state.settings || {};
        const mtmEnabled = !!s.mtm_snapshots_enabled;
        const toggle1 = document.getElementById('mtm-snapshots-toggle');
        const toggle2 = document.getElementById('mtm-snapshots-toggle-tab');
        if (toggle1) toggle1.checked = mtmEnabled;
        if (toggle2) toggle2.checked = mtmEnabled;
        const dp = document.getElementById('dash-flag-premium');
        const dsl = document.getElementById('dash-flag-strategy-sl');
        const dne = document.getElementById('dash-flag-non-expiry');
        const dm = document.getElementById('dash-flag-mtm');
        const volMode = document.getElementById('volatility-chart-mode');
        if (volMode && !volMode.dataset.bound) {
          volMode.dataset.bound = '1';
          volMode.addEventListener('change', function() { updateVolatilityMonitor(volmon); });
        }
        if (toggle1 && !toggle1.dataset.bound) {
          toggle1.dataset.bound = '1';
          toggle1.addEventListener('change', async function() {
            const enabled = toggle1.checked;
            await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mtm_snapshots_enabled: enabled }) });
            if (toggle2) toggle2.checked = enabled;
            if (dm) dm.checked = enabled;
          });
        }
        if (toggle2 && !toggle2.dataset.bound) {
          toggle2.dataset.bound = '1';
          toggle2.addEventListener('change', async function() {
            const enabled = toggle2.checked;
            await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mtm_snapshots_enabled: enabled }) });
            if (toggle1) toggle1.checked = enabled;
            if (dm) dm.checked = enabled;
          });
        }
        if (document.getElementById('dash-flags-save') && !document.getElementById('dash-flags-save').dataset.bound) {
          document.getElementById('dash-flags-save').dataset.bound = '1';
          if (dp) dp.checked = !!s.use_premium_based_strike;
          if (dsl) dsl.checked = !!s.strategy_sl_enabled;
          if (dne) dne.checked = !!s.trade_non_expiry_day;
          if (dm) dm.checked = mtmEnabled;
          document.getElementById('dash-flags-save').addEventListener('click', function() {
            document.getElementById('dash-auth-modal').style.display = 'flex';
            document.getElementById('dash-auth-user').value = '';
            document.getElementById('dash-auth-pass').value = '';
          });
          document.getElementById('dash-auth-cancel').addEventListener('click', function() {
            document.getElementById('dash-auth-modal').style.display = 'none';
          });
          document.getElementById('dash-auth-confirm').addEventListener('click', async function() {
            const user = document.getElementById('dash-auth-user').value.trim();
            const pass = document.getElementById('dash-auth-pass').value;
            if (!user || !pass) { alert('Enter username and password'); return; }
            const payload = {
              use_premium_based_strike: document.getElementById('dash-flag-premium').checked,
              strategy_sl_enabled: document.getElementById('dash-flag-strategy-sl').checked,
              trade_non_expiry_day: document.getElementById('dash-flag-non-expiry').checked,
              mtm_snapshots_enabled: document.getElementById('dash-flag-mtm').checked
            };
            try {
              const res = await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': 'Basic ' + btoa(user + ':' + pass) },
                body: JSON.stringify(payload)
              });
              if (!res.ok) throw new Error(await res.text() || res.statusText);
              document.getElementById('dash-auth-modal').style.display = 'none';
              if (toggle1) toggle1.checked = payload.mtm_snapshots_enabled;
              if (toggle2) toggle2.checked = payload.mtm_snapshots_enabled;
            } catch (e) { alert('Update failed: ' + (e.message || e)); }
          });
        }

        const stratVals = Object.values(stateStrategies);
        const portfolioSlHalt = stratVals.length > 0 && stratVals.every(s => s.status === 'CLOSED')
          && stratVals.some(s => String(s.message || '').includes('Portfolio SL hit'));
        const allDisabled = stratVals.length > 0 && stratVals.every(s => s.status === 'DISABLED');
        const dashBanner = document.getElementById('dash-disabled-banner');
        if (portfolioSlHalt && dashBanner) {
          dashBanner.innerHTML = '<div class="disabled-banner">Trading halted: portfolio stop-loss hit. Strategies are closed in state. The main loop may still run (see Scheduler health below).</div>';
        } else if (allDisabled) {
          dashBanner.innerHTML = '<div class="disabled-banner">Trading is disabled today (non-expiry day). No strategies will be executed.</div>';
        } else {
          dashBanner.innerHTML = '';
        }
        const marginWarning = stratVals.find(s => String(s.message || '').includes('MARGIN_NOT_AVAILABLE') || String(s.status || '') === 'ERROR');
        if (marginWarning && dashBanner && !portfolioSlHalt) {
          dashBanner.innerHTML = '<div class="warn-box">Margin gate warning: ' + (marginWarning.message || marginWarning.status || 'Check strategy status') + '</div>';
        }
        
        // Show error if expiry not available
        let errorHtml = '';
        if (idx.error) {
          errorHtml = `
            <div class="meta-item" style="grid-column: 1 / -1; background: #7c2d12; padding: 15px; border-radius: 6px; border-left: 4px solid #ef4444;">
              <div class="meta-label" style="color: #fed7aa;">⚠️ Status</div>
              <div class="meta-value" style="color: #fecaca; font-size: 14px; margin-top: 8px;">${idx.error}</div>
            </div>
          `;
        }
        
        document.getElementById('meta-info').innerHTML = errorHtml + `
          <div class="meta-item">
            <div class="meta-label">Index</div>
            <div class="meta-value">${idx.name || '-'}</div>
          </div>
          <div class="meta-item">
            <div class="meta-label">Expiry</div>
            <div class="meta-value">${idx.expiry || '-'}</div>
          </div>
          <div class="meta-item">
            <div class="meta-label">Spot</div>
            <div class="meta-value">${fmt(idx.spot)}</div>
          </div>
          <div class="meta-item">
            <div class="meta-label">Portfolio MTM</div>
            <div class="meta-value ${getClass(portfolio.mtm)}">${fmt(portfolio.mtm)}</div>
          </div>
          <div class="meta-item">
            <div class="meta-label">Available Margin</div>
            <div class="meta-value">${fmt(portfolio.available_margin)}</div>
          </div>
        `;

        updateVolatilityMonitor(volmon);

        document.getElementById('portfolio-status').innerHTML = `
          <div class="metric">
            <span class="metric-label">MTM</span>
            <span class="metric-value ${getClass(portfolio.mtm)}">${fmt(portfolio.mtm)}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Available Margin</span>
            <span class="metric-value">${fmt(portfolio.available_margin)}</span>
          </div>
          <div class="metric">
            <span class="metric-label">SL Limit</span>
            <span class="metric-value">${fmt(portfolio.sl_limit)}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Status</span>
            <span class="metric-value">${portfolio.status || '-'}</span>
          </div>
        `;

        const marginGateEl = document.getElementById('margin-gate-card');
        if (marginGateEl) {
          const isSensex = (idx.name || '').toUpperCase() === 'SENSEX';
          marginGateEl.innerHTML = `
            <div class="metric">
              <span class="metric-label">Available Margin</span>
              <span class="metric-value">${fmt(portfolio.available_margin)}</span>
            </div>
            <div class="metric">
              <span class="metric-label">Threshold Rule</span>
              <span class="metric-value">${isSensex ? '2.5L / lot + buffer' : '3.15L / lot + buffer'}</span>
            </div>
            <div class="subtext">If margin is short, the bot buys far OTM CE/PE hedges, waits 3 seconds, then rechecks before placing the straddle.</div>
          `;
        }

        const botMeta = state.bot || {};
        const survEn = botMeta.survivor_sl_to_cost_enabled;
        const survivorCostEl = document.getElementById('survivor-cost-card');
        if (survivorCostEl) {
          let survHtml = '<div class="metric"><span class="metric-label">Feature enabled</span><span class="metric-value">' +
            (survEn === true ? 'Yes' : survEn === false ? 'No' : '—') + '</span></div>';
          const openRows = [];
          for (const [name, st] of Object.entries(stateStrategies)) {
            if (st && st.status === 'OPEN') {
              const adj = !!st.survivor_sl_adjusted_to_cost;
              const hint = st.survivor_sl_to_cost_hint ? escHtml(String(st.survivor_sl_to_cost_hint)) : '';
              openRows.push(
                '<div style="margin-bottom:10px;border-bottom:1px solid #334155;padding-bottom:8px">' +
                '<div class="metric"><span class="metric-label">' + escHtml(name) + '</span><span class="metric-value">' +
                (adj ? 'Tightened to cost' : 'Not yet') + '</span></div>' +
                (hint ? '<div class="subtext" style="margin-top:4px">' + hint + '</div>' : '') +
                '</div>'
              );
            }
          }
          survHtml += openRows.length ? openRows.join('') : '<div class="subtext">No OPEN strategies in live state.</div>';
          survHtml += '<div class="subtext" style="margin-top:8px">Hints update every ~3s while the strategy is OPEN. If one leg is closed from DB restart, closed_via=RESTORED is set so adjust can run. Peer leg must be SL_FILLED/BROKER_SYNC/RESTORED.</div>';
          survivorCostEl.innerHTML = survHtml;
        }

        const sch = state.scheduler;
        const schedCard = document.getElementById('scheduler-card');
        const schedNotesEl = document.getElementById('scheduler-notes');
        if (schedNotesEl) {
          schedNotesEl.textContent = sch && sch.jobs_scheduled_at_startup === false && (sch.job_count === 0 || sch.job_count == null)
            ? 'No jobs were registered at startup (e.g. waiting for expiry). Retry thread may schedule later.'
            : '';
        }
        if (schedCard) {
          if (!sch) {
            schedCard.innerHTML = '<p class="meta-label">Scheduler diagnostics not available yet (snapshot callback not registered).</p>';
          } else if (sch.error) {
            schedCard.innerHTML = '<p class="warn-box">' + escHtml(sch.error) + '</p>';
          } else {
            const age = sch.heartbeat_age_sec;
            let hbClass = 'negative';
            let hbLabel = 'Stale or main loop stuck';
            if (age == null) {
              hbLabel = 'Unknown';
            } else if (age < 5) {
              hbClass = 'positive';
              hbLabel = 'OK — main loop alive';
            } else if (age < 30) {
              hbClass = '';
              hbLabel = 'Slow heartbeat';
            }
            let minimalBanner = '';
            if (sch.minimal_schedule_non_expiry) {
              minimalBanner = '<div class="warn-box" style="margin-bottom:12px;">Minimal schedule (non-expiry day): only margin updates + daily DB cleanup — no MTM monitor or strategy entries.</div>';
            }
            let jobRows = '<thead><tr><th>Job</th><th>Schedule</th><th>Next run</th><th>Last run</th></tr></thead><tbody>';
            for (const j of (sch.jobs || [])) {
              jobRows += '<tr><td>' + escHtml(j.label) + '<br/><span class="meta-label">' + escHtml(j.function) + '</span></td><td>' + escHtml(j.schedule) + '</td><td>' + fmtIso(j.next_run) + '</td><td>' + fmtIso(j.last_run) + '</td></tr>';
            }
            jobRows += '</tbody>';
            const sub = (sch.survivor_note ? '<p class="subtext" style="margin-top:10px">' + escHtml(sch.survivor_note) + '</p>' : '')
              + (sch.portfolio_sl_note ? '<p class="subtext">' + escHtml(sch.portfolio_sl_note) + '</p>' : '');
            schedCard.innerHTML = minimalBanner
              + '<div class="metric"><span class="metric-label">Main loop heartbeat</span><span class="metric-value ' + hbClass + '">' + hbLabel
              + (age != null && age !== undefined ? ' (' + age.toFixed(1) + 's ago)' : '') + '</span></div>'
              + '<div class="metric"><span class="metric-label">Registered jobs</span><span class="metric-value">' + (sch.job_count != null ? sch.job_count : 0) + '</span></div>'
              + '<table>' + jobRows + '</table>' + sub;
          }
        }

        const openStrategies = strategies.filter(s => s.status === 'OPEN').length;
        const totalPnL = trades.reduce((sum, t) => sum + (t.realized_pnl || 0), 0);
        document.getElementById('today-summary').innerHTML = `
          <div class="metric">
            <span class="metric-label">Open Strategies</span>
            <span class="metric-value">${openStrategies}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Closed Trades</span>
            <span class="metric-value">${trades.length}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Total P&L</span>
            <span class="metric-value ${getClass(totalPnL)}">${fmt(totalPnL)}</span>
          </div>
        `;

        let overviewHTML = '';
        for (const s of strategies.slice(0, 10)) {
          const strat_positions = positions.filter(p => p.strategy_id === s.id).length;
          const marginText = s.message || '-';
          const hedgeText = s.hedge_qty ? `Hedge qty: ${s.hedge_qty}${s.hedge_strikes ? ` | ${JSON.stringify(s.hedge_strikes)}` : ''}` : '';
          const expMargin = fmtMargin(calcRequiredMargin(s.lots, true));
          const nonExpMargin = fmtMargin(calcRequiredMargin(s.lots, false));
          overviewHTML += `
            <tr>
              <td>${s.strategy_name}</td>
              <td><span class="status-${s.status.toLowerCase()}">${s.status}</span></td>
              <td>${s.strike || '-'}</td>
              <td>${s.entry_time || '-'}</td>
              <td>${strat_positions} positions</td>
              <td>${expMargin} exp / ${nonExpMargin} non-exp${marginText !== '-' ? `<div class="subtext">${marginText}</div>` : ''}${hedgeText ? `<div class="subtext">${hedgeText}</div>` : ''}</td>
            </tr>
          `;
        }
        document.getElementById('overview-rows').innerHTML = overviewHTML || '<tr><td colspan="6">No data</td></tr>';

        let strategiesHTML = '';
        for (const s of strategies) {
          const hedgeText = s.hedge_qty ? `${s.hedge_qty}${s.hedge_strikes ? ` | ${JSON.stringify(s.hedge_strikes)}` : ''}` : '-';
          const expMargin = fmtMargin(calcRequiredMargin(s.lots, true));
          const nonExpMargin = fmtMargin(calcRequiredMargin(s.lots, false));
          strategiesHTML += `
            <tr>
              <td>${s.strategy_name}</td>
              <td>${s.execution_date || '-'}</td>
              <td>${s.strike || '-'}</td>
              <td>${s.entry_time || '-'}</td>
              <td><span class="status-${s.status.toLowerCase()}">${s.status}</span></td>
              <td>${s.lots || '-'}</td>
              <td>${fmt(s.leg_sl_pct)} %</td>
              <td>${fmt(s.strategy_sl)}</td>
              <td>${expMargin} exp / ${nonExpMargin} non-exp</td>
              <td>${s.message || '-'}</td>
              <td>${hedgeText}</td>
            </tr>
          `;
        }
        document.getElementById('strategies-rows').innerHTML = strategiesHTML || '<tr><td colspan="10">No data</td></tr>';

        let positionsHTML = '';
        for (const p of positions) {
          const strategy = strategies.find(s => s.id === p.strategy_id);
          const status = p.exit_price ? 'Closed' : 'Open';
          const pnl = p.exit_price ? (p.exit_price - p.entry_price) : 0;
          positionsHTML += `
            <tr>
              <td>${strategy?.strategy_name || '-'}</td>
              <td><span class="tag">${p.symbol || '-'}</span></td>
              <td>${p.quantity || '-'}</td>
              <td>${fmt(p.entry_price)}</td>
              <td>${p.target_price != null ? fmt(p.target_price) : '-'}</td>
              <td ${p.exit_price ? `class="${getClass(pnl)}"` : ''}>${fmt(p.exit_price) || '-'}</td>
              <td>${p.entry_time || '-'}</td>
              <td>${p.exit_time || '-'}</td>
              <td><span class="status-${status.toLowerCase()}">${status}</span></td>
            </tr>
          `;
        }
        document.getElementById('positions-rows').innerHTML = positionsHTML || '<tr><td colspan="9">No data</td></tr>';

        let ordersHTML = '';
        for (const o of orders.slice(0, 50)) {
          ordersHTML += `
            <tr>
              <td>${o.strategy_name || '-'}</td>
              <td><span class="tag">${(o.order_tag || '').substring(0, 20)}</span></td>
              <td>${o.symbol || '-'}</td>
              <td>${o.quantity || '-'}</td>
              <td>${o.order_type || '-'}</td>
              <td>${o.order_side || '-'}</td>
              <td>${o.status || '-'}</td>
              <td>${fmt(o.traded_price)}</td>
            </tr>
          `;
        }
        document.getElementById('orders-rows').innerHTML = ordersHTML || '<tr><td colspan="8">No data</td></tr>';

        let tradesHTML = '';
        for (const t of trades) {
          tradesHTML += `
            <tr>
              <td>${t.strategy_name || '-'}</td>
              <td>${t.execution_date || '-'}</td>
              <td>${t.strike || '-'}</td>
              <td>${t.entry_time || '-'}</td>
              <td>${t.exit_time || '-'}</td>
              <td class="${getClass(t.realized_pnl)}">${fmt(t.realized_pnl)}</td>
              <td class="${getClass(t.mtm_final)}">${fmt(t.mtm_final)}</td>
              <td>${t.reason || '-'}</td>
            </tr>
          `;
        }
        document.getElementById('trades-rows').innerHTML = tradesHTML || '<tr><td colspan="8">No data</td></tr>';

        let mtmHTML = '';
        for (const m of mtm.slice(-20)) {
          mtmHTML += `
            <tr>
              <td>${m.strategy_name || '-'}</td>
              <td class="${getClass(m.mtm)}">${fmt(m.mtm)}</td>
              <td class="${getClass(m.realized)}">${fmt(m.realized)}</td>
              <td class="${getClass(m.unrealized)}">${fmt(m.unrealized)}</td>
              <td>${m.timestamp || '-'}</td>
            </tr>
          `;
        }
        document.getElementById('mtm-rows').innerHTML = mtmHTML || '<tr><td colspan="5">No data</td></tr>';

        const logPathEl = document.getElementById('bot-log-path');
        const logPre = document.getElementById('bot-log-pre');
        const logMetaEl = document.getElementById('bot-log-meta');
        if (logPathEl && logPre && botlog) {
          logPathEl.textContent = botlog.path || '—';
          if (logMetaEl) {
            const ts = botlog.fetched_at_ms != null ? new Date(botlog.fetched_at_ms).toLocaleString() : new Date().toLocaleString();
            const shown = (botlog.lines && botlog.lines.length) || 0;
            const tailN = botlog.tail_line_count != null ? botlog.tail_line_count : '?';
            const mode = botlog.mode === 'all' ? 'full tail' : 'survivor filter';
            let disk = '';
            if (botlog.file_size_bytes != null && botlog.file_mtime_ms != null) {
              const kb = (botlog.file_size_bytes / 1024).toFixed(0);
              const mtime = new Date(botlog.file_mtime_ms).toLocaleString();
              disk = ' · on-disk: ' + kb + ' KB, last modified ' + mtime;
            }
            logMetaEl.textContent = 'Fetched ' + ts + disk + ' · ' + shown + ' line(s) shown (' + mode + ', last ' + tailN + ' raw lines from file end)';
          }
          if (botlog.error) {
            logPre.textContent = 'Error reading log: ' + botlog.error;
          } else if (botlog.missing) {
            logPre.textContent = 'Log file not found: ' + (botlog.path || '') + ' (set BOT_LOG_PATH to the same absolute path the bot uses, or ensure the bot process has created the file).';
          } else if (botlog.empty_file) {
            logPre.textContent = 'Log file exists but is empty — the bot may not have written yet, or the UI is reading a different path than the bot (check BOT_LOG_PATH).';
          } else if (botlog.filtered_empty) {
            const n = botlog.tail_line_count != null ? botlog.tail_line_count : '?';
            logPre.textContent = [
              'No survivor SL-to-cost lines in the last ' + n + ' line(s) of this tail.',
              '',
              'That is normal until one leg is stopped and the bot tightens the survivor SL to cost (WARNING in log). Older lines may rotate out of this tail.',
              '',
              'Tip: Overview tab → card "Survivor SL → cost" shows live state (feature on/off and per OPEN strategy: tightened or not) — more reliable than the log tail.',
              '',
              'Enable "Show full log" for the raw tail; Refresh only reloads.'
            ].join('\\n');
          } else {
            const lines = botlog.lines || [];
            const note = botlog.truncated ? '\\n\\n… (large file; showing tail only)\\n' : '';
            logPre.textContent = lines.join('\\n') + note;
          }
        }
        const logRefresh = document.getElementById('bot-log-refresh');
        if (logRefresh && !logRefresh.dataset.bound) {
          logRefresh.dataset.bound = '1';
          logRefresh.addEventListener('click', function() { loadDashboard(); });
        }
        const logShowFull = document.getElementById('bot-log-show-full');
        if (logShowFull && !logShowFull.dataset.bound) {
          logShowFull.dataset.bound = '1';
          logShowFull.addEventListener('change', function() { loadDashboard(); });
        }

        document.getElementById('update-time').textContent = new Date().toLocaleTimeString();
      } catch (err) {
        console.error('Dashboard load error:', err);
      }
    }

    loadDashboard();
    setInterval(loadDashboard, 2000);
  </script>
</body>
</html>
"""

HTML_TEMPLATE = """
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>XTS Bot MTM</title>
  <style>
    body { font-family: Georgia, 'Times New Roman', serif; margin: 24px; background: #f5f1e8; color: #1e1a14; }
    .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
    h1 { margin: 0; }
    .nav-link { padding: 8px 16px; background: #0ea5e9; color: white; text-decoration: none; border-radius: 4px; font-weight: bold; transition: background 0.3s; }
    .nav-link:hover { background: #0284c7; }
    .meta { margin-bottom: 16px; }
    table { width: 100%; border-collapse: collapse; background: #fffdf8; }
    th, td { padding: 10px 12px; border-bottom: 1px solid #e6ded2; text-align: left; }
    th { background: #efe6d6; }
    .negative { color: #a12222; font-weight: bold; }
    .positive { color: #206622; font-weight: bold; }
    .disabled-banner { background: #92400e; color: #fef3c7; padding: 14px 20px; border-radius: 6px; margin-bottom: 16px; font-size: 15px; font-weight: bold; text-align: center; border: 2px solid #d97706; }
  </style>
</head>
<body>
  <div class="header">
    <h1>XTS Bot MTM</h1>
    <a href="/dashboard" class="nav-link">📊 Full Dashboard</a>
  </div>
  <div id=\"disabled-banner\"></div>
  <div class=\"meta\" id=\"meta\"></div>
  <table>
    <thead>
      <tr>
        <th>Strategy</th>
        <th>Time</th>
        <th>Lots</th>
        <th>Leg SL %</th>
        <th>Leg target %</th>
        <th>Strategy SL</th>
        <th>Status</th>
        <th>MTM</th>
        <th>Strike</th>
      </tr>
    </thead>
    <tbody id=\"strategy-rows\"></tbody>
  </table>

  <section class="flags-section" style="margin-top: 24px; padding: 16px; background: #fffdf8; border: 1px solid #e6ded2; border-radius: 8px;">
    <h2 style="margin: 0 0 12px 0; font-size: 18px;">Trading flags</h2>
    <p style="color: #666; font-size: 13px; margin-bottom: 12px;">Changes require your login to apply. Only when both CE and PE strikes are in premium range will the straddle run.</p>
    <div class="flags-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; margin-bottom: 12px;">
      <label class="flag-row" style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
        <input type="checkbox" id="flag-use-premium-based-strike" />
        <span>Use premium-based strike (NIFTY 100±15, SENSEX 300±40)</span>
      </label>
      <label class="flag-row" style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
        <input type="checkbox" id="flag-strategy-sl-enabled" />
        <span>Strategy SL enabled (per-strategy stop-loss)</span>
      </label>
      <label class="flag-row" style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
        <input type="checkbox" id="flag-trade-non-expiry-day" />
        <span>Trade on non-expiry day</span>
      </label>
      <label class="flag-row" style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
        <input type="checkbox" id="flag-mtm-snapshots" />
        <span>MTM snapshots to DB</span>
      </label>
    </div>
    <button type="button" id="flags-save-btn" style="padding: 8px 16px; background: #0ea5e9; color: white; border: none; border-radius: 4px; font-weight: bold; cursor: pointer;">Save (requires login)</button>
  </section>

  <div id="flags-auth-modal" style="display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 1000; align-items: center; justify-content: center;">
    <div style="background: #fffdf8; padding: 24px; border-radius: 8px; max-width: 320px; width: 90%; border: 1px solid #e6ded2;">
      <p style="margin: 0 0 12px 0; font-weight: bold;">Confirm: enter username and password</p>
      <input type="text" id="flags-auth-user" placeholder="Username" style="width: 100%; padding: 8px; margin-bottom: 8px; box-sizing: border-box;" />
      <input type="password" id="flags-auth-pass" placeholder="Password" style="width: 100%; padding: 8px; margin-bottom: 12px; box-sizing: border-box;" />
      <div style="display: flex; gap: 8px;">
        <button type="button" id="flags-auth-confirm" style="padding: 8px 16px; background: #0ea5e9; color: white; border: none; border-radius: 4px; cursor: pointer;">Apply</button>
        <button type="button" id="flags-auth-cancel" style="padding: 8px 16px; background: #94a3b8; color: white; border: none; border-radius: 4px; cursor: pointer;">Cancel</button>
      </div>
    </div>
  </div>

  <script>
    function fmt(num) {
      if (num === null || num === undefined) return "-";
      return Number(num).toFixed(2);
    }

    async function loadFlags() {
      try {
        const res = await fetch('/api/settings', { cache: 'no-store', credentials: 'same-origin' });
        const s = await res.json();
        document.getElementById('flag-use-premium-based-strike').checked = !!s.use_premium_based_strike;
        document.getElementById('flag-strategy-sl-enabled').checked = !!s.strategy_sl_enabled;
        document.getElementById('flag-trade-non-expiry-day').checked = !!s.trade_non_expiry_day;
        document.getElementById('flag-mtm-snapshots').checked = !!s.mtm_snapshots_enabled;
      } catch (e) { console.error('Load flags:', e); }
    }

    document.getElementById('flags-save-btn').addEventListener('click', function() {
      document.getElementById('flags-auth-modal').style.display = 'flex';
      document.getElementById('flags-auth-user').value = '';
      document.getElementById('flags-auth-pass').value = '';
    });
    document.getElementById('flags-auth-cancel').addEventListener('click', function() {
      document.getElementById('flags-auth-modal').style.display = 'none';
    });
    document.getElementById('flags-auth-confirm').addEventListener('click', async function() {
      const user = document.getElementById('flags-auth-user').value.trim();
      const pass = document.getElementById('flags-auth-pass').value;
      if (!user || !pass) { alert('Enter username and password'); return; }
      const payload = {
        use_premium_based_strike: document.getElementById('flag-use-premium-based-strike').checked,
        strategy_sl_enabled: document.getElementById('flag-strategy-sl-enabled').checked,
        trade_non_expiry_day: document.getElementById('flag-trade-non-expiry-day').checked,
        mtm_snapshots_enabled: document.getElementById('flag-mtm-snapshots').checked
      };
      try {
        const res = await fetch('/api/settings', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Basic ' + btoa(user + ':' + pass)
          },
          body: JSON.stringify(payload)
        });
        if (!res.ok) { const t = await res.text(); throw new Error(t || res.statusText); }
        document.getElementById('flags-auth-modal').style.display = 'none';
        await loadFlags();
      } catch (e) {
        alert('Update failed: ' + (e.message || e));
      }
    });

    async function refresh() {
      const res = await fetch('/state', { cache: 'no-store', credentials: 'same-origin' });
      const data = await res.json();

      const idx = data.index || {};
      const portfolio = data.portfolio || {};
      const strategies = data.strategies || {};

      const allDisabled = Object.values(strategies).length > 0 && Object.values(strategies).every(s => s.status === 'DISABLED');
      const bannerEl = document.getElementById('disabled-banner');
      if (allDisabled) {
        bannerEl.innerHTML = '<div class="disabled-banner">Trading is disabled today (non-expiry day). No strategies will be executed.</div>';
      } else {
        bannerEl.innerHTML = '';
      }
        const marginWarning = Object.values(strategies).find(s => String(s.message || '').includes('MARGIN_NOT_AVAILABLE') || String(s.status || '') === 'ERROR');
        if (marginWarning && bannerEl) {
          bannerEl.innerHTML = '<div class="warn-box">Margin gate warning: ' + (marginWarning.message || marginWarning.status || 'Check strategy status') + '</div>';
        }

      const mtmClass = portfolio.mtm < 0 ? 'negative' : 'positive';
      document.getElementById('meta').innerHTML =
        `Index: <b>${idx.name || '-'}</b> | Expiry: <b>${idx.expiry || '-'}</b> | Spot: <b>${idx.spot || '-'}</b><br>` +
        `Portfolio MTM: <span class="${mtmClass}">${fmt(portfolio.mtm)}</span> | Available Margin: ${fmt(portfolio.available_margin)} | SL Limit: ${fmt(portfolio.sl_limit)} | Updated: ${portfolio.last_update || '-'}`;

      const tbody = document.getElementById('strategy-rows');
      tbody.innerHTML = '';
      Object.values(strategies).forEach((s) => {
        const row = document.createElement('tr');
        const mtmClass = s.mtm < 0 ? 'negative' : 'positive';
        row.innerHTML = `
          <td>${s.name}</td>
          <td>${s.time}</td>
          <td>${s.lots}</td>
          <td>${s.leg_sl_pct}</td>
          <td>${s.leg_target_pct != null ? s.leg_target_pct : '-'}</td>
          <td>${fmt(s.strategy_sl)}</td>
          <td>${s.status || '-'}</td>
          <td class="${mtmClass}">${fmt(s.mtm)}</td>
          <td>${s.strike || '-'}</td>
        `;
        tbody.appendChild(row);
      });
    }

    loadFlags();
    refresh();
    setInterval(refresh, 3000);
  </script>
</body>
</html>
"""


def create_app(username: str, password: str) -> Flask:
    app = Flask(__name__)
    app.config["BASIC_AUTH_USERNAME"] = username
    app.config["BASIC_AUTH_PASSWORD"] = password
    basic_auth = BasicAuth(app)

    @app.after_request
    def _no_cache_live_data(response):
        """Avoid stale dashboard JSON / bot log tail from browser or proxy caches."""
        p = request.path or ""
        if p.startswith("/api/") or p in ("/state", "/dashboard"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.route("/")
    @basic_auth.required
    def index():
        return render_template_string(HTML_TEMPLATE)

    @app.route("/dashboard")
    @basic_auth.required
    def dashboard():
        return render_template_string(DASHBOARD_TEMPLATE)

    @app.route("/state")
    @basic_auth.required
    def state():
        return jsonify(get_snapshot())

    @app.route("/api/settings", methods=["GET", "POST"])
    @basic_auth.required
    def api_settings():
        """Get or update UI-editable trading flags. POST requires valid Basic Auth (re-enter credentials in UI modal)."""
        from flask import request
        if request.method == "POST":
            try:
                data = request.get_json(force=True, silent=True) or {}
                if "mtm_snapshots_enabled" in data:
                    set_mtm_snapshots_enabled(bool(data["mtm_snapshots_enabled"]))
                if "use_premium_based_strike" in data:
                    set_trading_flag("use_premium_based_strike", bool(data["use_premium_based_strike"]))
                if "strategy_sl_enabled" in data:
                    set_trading_flag("strategy_sl_enabled", bool(data["strategy_sl_enabled"]))
                if "trade_non_expiry_day" in data:
                    set_trading_flag("trade_non_expiry_day", bool(data["trade_non_expiry_day"]))
            except Exception as e:
                return jsonify({"error": str(e)}), 400
        return jsonify(get_all_trading_flags())

    @app.route("/api/strategies")
    @basic_auth.required
    def api_strategies():
        """Get all strategies from database."""
        try:
            conn = sqlite3.connect("trades.db")
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM strategies ORDER BY id DESC")
            rows = cursor.fetchall()
            conn.close()
            return jsonify([dict(row) for row in rows])
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/positions")
    @basic_auth.required
    def api_positions():
        """Get all positions from database. Adds target_price (65%% profit on entry premium) for UI."""
        try:
            conn = sqlite3.connect("trades.db")
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions ORDER BY id DESC")
            rows = cursor.fetchall()
            conn.close()
            out = []
            for row in rows:
                d = dict(row)
                ep = d.get("entry_price")
                if ep is not None and float(ep) > 0:
                    d["target_price"] = round(float(ep) * (1 - LEG_TARGET_PCT / 100.0), 2)
                else:
                    d["target_price"] = None
                out.append(d)
            return jsonify(out)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/orders")
    @basic_auth.required
    def api_orders():
        """Get all orders from database."""
        try:
            conn = sqlite3.connect("trades.db")
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 100")
            rows = cursor.fetchall()
            conn.close()
            return jsonify([dict(row) for row in rows])
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/trades")
    @basic_auth.required
    def api_trades():
        """Get all closed trades from database."""
        try:
            conn = sqlite3.connect("trades.db")
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades_closed ORDER BY id DESC LIMIT 100")
            rows = cursor.fetchall()
            conn.close()
            return jsonify([dict(row) for row in rows])
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/mtm")
    @basic_auth.required
    def api_mtm():
        """Get MTM snapshots from database."""
        try:
            conn = sqlite3.connect("trades.db")
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM mtm_snapshots ORDER BY id DESC LIMIT 100")
            rows = cursor.fetchall()
            conn.close()
            return jsonify([dict(row) for row in rows])
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/volatility-monitor")
    @basic_auth.required
    def api_volatility_monitor():
        """Latest 1m spot bars + calm metrics for the bot's active index (NIFTY/SENSEX)."""
        try:
            snap = get_snapshot()
            idx = snap.get("index") or {}
            name = (idx.get("name") or "NIFTY").strip().upper()
            if name not in ("NIFTY", "SENSEX"):
                name = "NIFTY"
            rows = fetch_spot_market_rows(name, 120)
            return jsonify(
                {
                    "index": name,
                    "rows": rows,
                    "bar_unix_offset_sec": CALM_ZONE_BAR_UNIX_OFFSET_SEC,
                }
            )
        except Exception as e:
            return (
                jsonify(
                    {
                        "index": "NIFTY",
                        "rows": [],
                        "error": str(e),
                        "bar_unix_offset_sec": CALM_ZONE_BAR_UNIX_OFFSET_SEC,
                    }
                ),
                500,
            )

    @app.route("/api/bot-log")
    @basic_auth.required
    def api_bot_log():
        """Tail of bot log file: BOT_LOG_PATH if set, else bot.log next to ui.py (same default as bot)."""
        from flask import request

        try:
            n = int(request.args.get("lines", "800"))
        except ValueError:
            n = 800
        n = max(50, min(n, 5000))
        mode = (request.args.get("mode") or "survivor").strip().lower()
        if mode not in ("survivor", "all"):
            mode = "survivor"
        return jsonify(read_bot_log_tail(max_lines=n, mode=mode))

    # Flask/Werkzeug: access lines must not hit bot.log (see silence_werkzeug_http_access_log docstring).
    ensure_http_access_not_logged()
    import logging as _logging

    app.logger.setLevel(_logging.WARNING)

    return app
