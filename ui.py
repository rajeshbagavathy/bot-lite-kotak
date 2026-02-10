from flask import Flask, jsonify, render_template_string
from flask_basicauth import BasicAuth

from state import get_snapshot

HTML_TEMPLATE = """
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>XTS Bot MTM</title>
  <style>
    body { font-family: Georgia, 'Times New Roman', serif; margin: 24px; background: #f5f1e8; color: #1e1a14; }
    h1 { margin-bottom: 8px; }
    .meta { margin-bottom: 16px; }
    table { width: 100%; border-collapse: collapse; background: #fffdf8; }
    th, td { padding: 10px 12px; border-bottom: 1px solid #e6ded2; text-align: left; }
    th { background: #efe6d6; }
    .negative { color: #a12222; font-weight: bold; }
    .positive { color: #206622; font-weight: bold; }
  </style>
</head>
<body>
  <h1>XTS Bot MTM</h1>
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

      const mtmClass = portfolio.mtm < 0 ? 'negative' : 'positive';
      document.getElementById('meta').innerHTML =
        `Index: <b>${idx.name || '-'}</b> | Expiry: <b>${idx.expiry || '-'}</b> | Spot: <b>${idx.spot || '-'}</b><br>` +
        `Portfolio MTM: <span class="${mtmClass}">${fmt(portfolio.mtm)}</span> | SL Limit: ${fmt(portfolio.sl_limit)} | Updated: ${portfolio.last_update || '-'}`;

      const tbody = document.getElementById('strategy-rows');
      tbody.innerHTML = '';
      const strategies = data.strategies || {};
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

    @app.route("/state")
    @basic_auth.required
    def state():
        return jsonify(get_snapshot())

    return app
