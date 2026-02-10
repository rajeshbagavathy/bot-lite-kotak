# XTS Bot Lite - Minimal Straddle Trading Bot

A simplified, production-ready straddle selling bot for XTS 5Paisa API. Automatically sells straddles at four fixed times daily with individual strategy SLs (20-35% per leg) and a portfolio-level stop loss at -80,000.

## Features

- **Time-Based Strategies**: Executes straddle selling at 09:20, 10:01, 12:40, and 13:50 with configurable lot sizes
- **Dual Index Support**: Auto-selects between NIFTY and SENSEX based on closest expiry (SENSEX preferred on tie)
- **Strategy-Level SLs**: Individual SL thresholds per strategy (16k/30k/16k/16k)
- **Per-Leg SLs**: 20% SL for morning strategies, 35% for afternoon
- **Portfolio SL**: Automatic square-off of all positions when portfolio MTM reaches -80,000
- **Real-Time MTM Monitoring**: Calculates realized + unrealized P&L every 3 seconds
- **BasicAuth Web UI**: Monitor MTM, strategy status, and strike in real time
- **Clean, Minimal Codebase**: ~700 lines across 5 core modules (vs. 3000+ lines in original)

## Project Structure

```
xts-bot-lite/
├── bot.py                 # Main scheduler and execution logic
├── config.py              # Strategies, indices, credentials  
├── xts_client.py          # XTS API wrapper (clean, minimal)
├── mtm.py                 # MTM calculation (realized + unrealized)
├── state.py               # Thread-safe state management
├── ui.py                  # Flask BasicAuth web dashboard
├── Connect.py             # XTS API (copied from original)
├── Exception.py           # XTS exceptions (copied from original)
├── config.ini             # XTS endpoint config
├── requirements.txt       # Python dependencies
├── docs/                  # 📚 All Documentation (organized)
│   ├── QUICKSTART.md             # Quick start guide
│   ├── DEPLOYMENT.md             # EC2 deployment guide
│   ├── PROJECT_SUMMARY.md        # Architecture overview
│   ├── PROJECT_ORGANIZATION.md   # Project structure details
│   ├── fixes/                    # Technical fix documentation
│   │   ├── EXCHANGE_SEGMENT_FIX.md
│   │   └── API_PARAMETER_FIX.md
│   └── README.md                 # Documentation index
├── tests/                 # 🧪 Validation & Testing
│   ├── test_all_config.py        # Comprehensive validation ⭐
│   ├── test_segments.py          # Exchange segment validation
│   ├── test_lot_sizes.py         # Lot size validation
│   ├── test_api_params.py        # API parameter validation
│   ├── test_expiry_debug.py      # Real API connectivity test
│   ├── preflight-check.sh        # Pre-deployment checks
│   └── README.md                 # Test documentation
└── README.md              # This file
```

**See [docs/PROJECT_ORGANIZATION.md](docs/PROJECT_ORGANIZATION.md) for complete details**

## Installation

### 1. Install Dependencies

```bash
cd xts-bot-lite
pip install -r requirements.txt
```

### 2. Set Credentials

**Option A: Environment Variables (Local Development)**
```bash
export ACC_NAME="your_account_name"
export XTS_API_KEY_5P="your_api_key"
export XTS_API_SECRET_5P="your_api_secret"
export XTS_5P_CLIENTID_5P="your_client_id"
export XTS_MARKET_API_KEY_5P="your_market_api_key"
export XTS_MARKET_API_SECRET_5P="your_market_api_secret"
export LOGIN_USERNAME_5P="your_login_username"
export LOGIN_PASSWORD_5P="your_login_password"
export BASIC_AUTH_USERNAME="ui_username"
export BASIC_AUTH_PASSWORD="ui_password"
```

**Option B: AWS SSM Parameter Store (EC2 Production)**

The bot will automatically fetch credentials from AWS SSM Parameter Store if environment variables are not set. Ensure the EC2 instance has an IAM role with `ssm:GetParameter` permission and the following parameters exist:

Use account-scoped parameter paths like:

```
/trade/config/${ACC_NAME}/apikey
/trade/config/${ACC_NAME}/apisecret
/trade/config/${ACC_NAME}/clientid
/trade/config/${ACC_NAME}/marketdataapikey
/trade/config/${ACC_NAME}/marketdataapisecret
/trade/config/${ACC_NAME}/loginusername
/trade/config/${ACC_NAME}/loginpassword
```

## Configuration

Edit `config.py` to customize:

**Strategies** (modify `STRATEGIES` list):
```python
STRATEGIES = [
    StrategyConfig("S0920", "09:20:00", 8, 20.0, 16000.0),      # 9:20 AM, 8 lots, 20% leg SL, 16k strategy SL
    StrategyConfig("S1001", "10:01:00", 16, 20.0, 30000.0),     # 10:01 AM, 16 lots, 20% leg SL, 30k strategy SL
    StrategyConfig("S1240", "12:40:00", 8, 35.0, 16000.0),      # 12:40 PM, 8 lots, 35% leg SL, 16k strategy SL
    StrategyConfig("S1350", "13:50:00", 8, 35.0, 16000.0),      # 1:50 PM, 8 lots, 35% leg SL, 16k strategy SL
]
```

**Portfolio Stop Loss** (modify `PORTFOLIO_SL_LIMIT`):
```python
PORTFOLIO_SL_LIMIT = -80000.0  # Square off all positions when portfolio MTM < -80k
```

## Running the Bot

### Local/Development

```bash
python bot.py
```

The bot will:
1. Login to XTS API (interactive + market data)
2. Select the nearest expiry for NIFTY or SENSEX
3. Start the scheduler
4. Launch the web UI at `http://localhost:8001`

### AWS EC2 (Production)

**Create a startup script** (`/opt/xts-bot/start.sh`):
```bash
#!/bin/bash
cd /opt/xts-bot
source venv/bin/activate
python bot.py > bot.log 2>&1 &
```

**Add to EC2 user data** (or cron):
```bash
#!/bin/bash
cd /opt/xts-bot
nohup python bot.py > nohup.out 2>&1 &
```

**Or use systemd** (recommended):

Create `/etc/systemd/system/xts-bot.service`:
```ini
[Unit]
Description=XTS Straddle Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/xts-bot
ExecStart=/usr/bin/python3 /opt/xts-bot/bot.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/xts-bot.log
StandardError=append:/var/log/xts-bot.log

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable xts-bot
sudo systemctl start xts-bot
sudo systemctl status xts-bot
```

View logs:
```bash
sudo journalctl -u xts-bot -f
# or
tail -f /var/log/xts-bot.log
```

## Web UI

Access the dashboard at `http://<ec2-ip>:8001` with BasicAuth credentials.

**Displays**:
- **Index & Expiry**: Selected index and option contract expiry
- **Spot Price**: Current ATM reference price
- **Portfolio MTM**: Total realized + unrealized P&L
- **Strategy Status**: Per-strategy execution status, entry time, MTM, and strike
- **Real-Time Updates**: Refreshes every 3 seconds

## How It Works

### Startup Flow

1. **Load credentials** from environment or AWS SSM
2. **Login to XTS** (interactive API for orders, market data API for LTP)
3. **Fetch expiry dates** for NIFTY and SENSEX
4. **Select index**: Closest expiry; if tied, prefer SENSEX
5. **Initialize state**: Strategy trackers for all 4 timed executions
6. **Start scheduler**: Jobs run at 09:20, 10:01, 12:40, 13:50 (IST)
7. **Launch web UI**: Flask app on port 8001

### Strategy Execution

When a scheduled time arrives:
1. **Get spot price** via market data API
2. **Calculate ATM strike** (nearest strike divisible by strike_diff)
3. **Fetch CE/PE instrument IDs** for ATM strike from option chain
4. **Place sell orders** (MIS) for both legs at market price
5. **Wait 5 seconds** for fills
6. **Place SL orders** (stop-limit) at leg_sl_pct% above entry price
7. **Update state**: Strategy marked as OPEN with strike and SL order IDs

### MTM Monitoring (every 3 seconds)

1. **Fetch daywise positions** from interactive API
2. **Get LTP** for all position instruments
3. **Calculate MTM**:
   - Realized: Squared-off portions (buy/sell overlap)
   - Unrealized: Open positions marked to market
4. **Per-strategy checks**:
   - If strategy MTM <= -strategy_sl → close all legs for that strategy
5. **Portfolio checks**:
   - If portfolio MTM <= -80,000 → square-off ALL open positions
   - Reset all strategies to CLOSED
6. **Update UI state** with current values

### Stop Loss Execution

- **Per-leg SL**: Placed as stop-limit orders at entry_price * (1 + leg_sl_pct/100)
- **Strategy SL**: Triggered by negative MTM; closes both CE and PE legs
- **Portfolio SL**: Triggers immediate market square-off of all positions

## State Persistence

The bot maintains in-memory state (no pickle files). On restart:
- Reconnects to XTS API
- Re-selects expiry and index
- Resets all strategy states to PENDING
- Previous day's orders are not recovered (intentional design)

To persist state across restarts, modify `state.py` to add pickle checkpoint logic.

## Logs

Logs are written to `bot.log` with format:
```
2025-02-08 09:20:15 - INFO - Strategy S0920 execution started
2025-02-08 09:20:20 - INFO - Strategy S0920 opened with strike 19850
2025-02-08 10:05:00 - INFO - Portfolio MTM: 2500.25
2025-02-08 14:30:00 - ERROR - Strategy SL hit for S1001
```

## Troubleshooting

### Bot doesn't execute orders at scheduled time
- Ensure EC2 system time is correct (compare with `date` in EC2 vs local)
- Check that the XTS API credentials are correctly set
- Review `bot.log` for authentication errors

### MTM calculation seems wrong
- Verify that positions are fetched correctly: Check the XTS order book and compare with UI
- Ensure LTP subscription is working: Check market data session login
- Review `mtm.py` calculation logic for your position mix

### Web UI shows no data
- Ensure Flask is running: Check `ps aux | grep python`
- Verify port 8001 is open: `sudo ufw allow 8001` (if using ufw)
- Test BasicAuth: `curl -u username:password http://localhost:8001/state`

### API errors (429, 401, 500)
- Check XTS API rate limits and backoff appropriately
- Verify credentials and tokens in AWS SSM
- Check XTS API documentation for breaking changes

## Documentation

Comprehensive documentation is available in the `docs/` directory:

- **[Quick Start Guide](docs/QUICKSTART.md)** - Fast setup for development
- **[Deployment Guide](docs/DEPLOYMENT.md)** - EC2 production deployment with systemd, Nginx, SSL
- **[Project Summary](docs/PROJECT_SUMMARY.md)** - Architecture overview and design decisions
- **[Technical Fixes](docs/fixes/)** - Exchange segment and API parameter fixes

## Testing & Validation

Run validation tests to verify configuration:

```bash
# From project root
python3 tests/test_all_config.py    # ⭐ Comprehensive validation (recommended)
python3 tests/test_segments.py      # Exchange segment validation
python3 tests/test_lot_sizes.py     # Lot size validation
python3 tests/test_api_params.py    # API parameter validation

# Pre-flight checks
cd tests && ./preflight-check.sh    # Full pre-deployment verification
```

**Expected Output:**
```
✅ ALL VALIDATIONS PASSED! 🎯 Configuration is production-ready!
```

See [tests/README.md](tests/README.md) for detailed test documentation.

## Performance Notes

- **Memory**: ~50-100 MB (minimal state, no CSV logging)
- **CPU**: <5% (scheduler-based, not continuous polling)
- **API Calls**: ~10-15 per minute (strategy execution + MTM check)
- **Network**: <1 Mbps (JSON payloads only)

## Upgrade from Original Bot

Key simplifications from the original 3000+ line codebase:

| Feature | Original | xts-bot-lite |
|---------|----------|--------------|
| Code Lines | 3000+ | ~700 |
| Strategies | Multiple (straddle/strangle/ironfly) | Straddle only |
| Indices | 6 (NIFTY, BANKNIFTY, SENSEX, etc.) | 2 (NIFTY, SENSEX) |
| Hedge Orders | Yes | No |
| CSV Logging | Yes | No (in-memory only) |
| Ratio Balancing | Yes | No |
| Dynamic Lot Sizing | Yes | Fixed per strategy |
| ITM Logic | Yes | No |
| State Persistence | Pickle files | In-memory (add manually) |
| SL Trailing | Complex | Simple per-leg |

## License

Same as original XTS bot.

## Support

Contact: [Your contact info]
Repo: [Your git repo URL if applicable]
