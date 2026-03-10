from flask import Flask, jsonify, render_template_string
from flask_basicauth import BasicAuth
import sqlite3

from state import get_snapshot, get_mtm_snapshots_enabled, set_mtm_snapshots_enabled

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
    .refresh-time { color: #64748b; font-size: 11px; margin-top: 20px; text-align: center; }
  </style>
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
          <div class="card-title">Settings</div>
          <div id="settings-card">
            <label class="setting-row">
              <input type="checkbox" id="mtm-snapshots-toggle" />
              <span>Enable MTM snapshots</span>
            </label>
            <div class="meta-label" style="margin-top: 6px; font-size: 11px;">Writes strategy MTM to DB every ~60s when enabled. Turn on to populate the MTM Snapshots tab.</div>
          </div>
        </div>
      </div>
      <table>
        <thead>
          <tr><th>Strategy</th><th>Status</th><th>Strike</th><th>Entry Time</th><th>Positions</th></tr>
        </thead>
        <tbody id="overview-rows">Loading...</tbody>
      </table>
    </div>

    <div id="strategies" class="tab-content">
      <table>
        <thead>
          <tr><th>Strategy Name</th><th>Execution Date</th><th>Strike</th><th>Entry Time</th><th>Status</th><th>Lots</th><th>Leg SL %</th><th>Strategy SL</th></tr>
        </thead>
        <tbody id="strategies-rows">Loading...</tbody>
      </table>
    </div>

    <div id="positions" class="tab-content">
      <table>
        <thead>
          <tr><th>Strategy</th><th>Symbol</th><th>Quantity</th><th>Entry Price</th><th>Exit Price</th><th>Entry Time</th><th>Exit Time</th><th>Status</th></tr>
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

    function switchTab(event, tabName) {
      document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.getElementById(tabName).classList.add('active');
      event.target.classList.add('active');
    }

    async function loadDashboard() {
      try {
        const [state, strategies, positions, orders, trades, mtm] = await Promise.all([
          fetch('/state').then(r => r.json()),
          fetch('/api/strategies').then(r => r.json()),
          fetch('/api/positions').then(r => r.json()),
          fetch('/api/orders').then(r => r.json()),
          fetch('/api/trades').then(r => r.json()),
          fetch('/api/mtm').then(r => r.json()),
        ]);

        const portfolio = state.portfolio || {};
        const idx = state.index || {};
        const stateStrategies = state.strategies || {};

        const mtmEnabled = !!(state.settings && state.settings.mtm_snapshots_enabled);
        const toggle1 = document.getElementById('mtm-snapshots-toggle');
        const toggle2 = document.getElementById('mtm-snapshots-toggle-tab');
        if (toggle1) toggle1.checked = mtmEnabled;
        if (toggle2) toggle2.checked = mtmEnabled;
        if (toggle1 && !toggle1.dataset.bound) {
          toggle1.dataset.bound = '1';
          toggle1.addEventListener('change', async function() {
            const enabled = toggle1.checked;
            await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mtm_snapshots_enabled: enabled }) });
            if (toggle2) toggle2.checked = enabled;
          });
        }
        if (toggle2 && !toggle2.dataset.bound) {
          toggle2.dataset.bound = '1';
          toggle2.addEventListener('change', async function() {
            const enabled = toggle2.checked;
            await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mtm_snapshots_enabled: enabled }) });
            if (toggle1) toggle1.checked = enabled;
          });
        }

        const allDisabled = Object.values(stateStrategies).length > 0 && Object.values(stateStrategies).every(s => s.status === 'DISABLED');
        const dashBanner = document.getElementById('dash-disabled-banner');
        if (allDisabled) {
          dashBanner.innerHTML = '<div class="disabled-banner">Trading is disabled today (non-expiry day). No strategies will be executed.</div>';
        } else {
          dashBanner.innerHTML = '';
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
          overviewHTML += `
            <tr>
              <td>${s.strategy_name}</td>
              <td><span class="status-${s.status.toLowerCase()}">${s.status}</span></td>
              <td>${s.strike || '-'}</td>
              <td>${s.entry_time || '-'}</td>
              <td>${strat_positions} positions</td>
            </tr>
          `;
        }
        document.getElementById('overview-rows').innerHTML = overviewHTML || '<tr><td colspan="5">No data</td></tr>';

        let strategiesHTML = '';
        for (const s of strategies) {
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
            </tr>
          `;
        }
        document.getElementById('strategies-rows').innerHTML = strategiesHTML || '<tr><td colspan="8">No data</td></tr>';

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
              <td ${p.exit_price ? `class="${getClass(pnl)}"` : ''}>${fmt(p.exit_price) || '-'}</td>
              <td>${p.entry_time || '-'}</td>
              <td>${p.exit_time || '-'}</td>
              <td><span class="status-${status.toLowerCase()}">${status}</span></td>
            </tr>
          `;
        }
        document.getElementById('positions-rows').innerHTML = positionsHTML || '<tr><td colspan="8">No data</td></tr>';

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
        <th>Strategy SL</th>
        <th>Status</th>
        <th>MTM</th>
        <th>Strike</th>
      </tr>
    </thead>
    <tbody id=\"strategy-rows\"></tbody>
  </table>

  <script>
    function fmt(num) {
      if (num === null || num === undefined) return "-";
      return Number(num).toFixed(2);
    }

    async function refresh() {
      const res = await fetch('/state');
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
          <td>${fmt(s.strategy_sl)}</td>
          <td>${s.status || '-'}</td>
          <td class="${mtmClass}">${fmt(s.mtm)}</td>
          <td>${s.strike || '-'}</td>
        `;
        tbody.appendChild(row);
      });
    }

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
        """Get or update UI-editable settings (e.g. MTM snapshot enabled)."""
        from flask import request
        if request.method == "POST":
            try:
                data = request.get_json(force=True, silent=True) or {}
                if "mtm_snapshots_enabled" in data:
                    set_mtm_snapshots_enabled(bool(data["mtm_snapshots_enabled"]))
            except Exception as e:
                return jsonify({"error": str(e)}), 400
        return jsonify({"mtm_snapshots_enabled": get_mtm_snapshots_enabled()})

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
        """Get all positions from database."""
        try:
            conn = sqlite3.connect("trades.db")
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions ORDER BY id DESC")
            rows = cursor.fetchall()
            conn.close()
            return jsonify([dict(row) for row in rows])
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

    return app
