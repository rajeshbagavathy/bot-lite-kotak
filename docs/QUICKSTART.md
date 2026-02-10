# Quick Start Guide - XTS Bot Lite

## 60-Second Setup

### 1. Install Dependencies
```bash
cd xts-bot-lite
pip install -r requirements.txt
```

### 2. Set Your Credentials
```bash
# Option A: Environment Variables
export XTS_API_KEY_5P="your_api_key"
export XTS_API_SECRET_5P="your_api_secret"
export XTS_5P_CLIENTID_5P="your_client_id"
export XTS_MARKET_API_KEY_5P="your_market_key"
export XTS_MARKET_API_SECRET_5P="your_market_secret"
export LOGIN_USERNAME_5P="your_username"
export LOGIN_PASSWORD_5P="your_password"
export BASIC_AUTH_USERNAME="admin"
export BASIC_AUTH_PASSWORD="password123"

# Option B: AWS EC2 (no env vars needed)
# Just ensure EC2 has IAM role with SSM read access
# and credentials are stored in SSM Parameter Store
```

### 3. Run the Bot
```bash
python bot.py
```

### 4. Access the Dashboard
Open your browser: `http://localhost:8001`
- Login with BasicAuth credentials from Step 2
- Watch MTM update in real-time
- Monitor strategy execution at 09:20, 10:01, 12:40, 13:50

## What Happens Next

📍 **Startup** (takes ~5 seconds)
- Connects to XTS API
- Selects closest expiry (NIFTY or SENSEX)
- Initializes 4 strategies to PENDING

🎯 **At 09:20 AM** (and 10:01, 12:40, 13:50)
- Bot detects scheduled time
- Calculates ATM strike
- Places sell orders for CE and PE
- Waits for fills
- Places SL orders at configured percentages

📊 **Every 3 Seconds**
- Fetches positions and LTP
- Calculates portfolio MTM
- Updates strategy-level MTM
- Checks if any SLs have been hit
- Updates the web UI

🛑 **Risk Management**
- If any strategy MTM < -strategy_sl → Close that strategy
- If portfolio MTM < -80,000 → Close ALL positions
- All positions squared off by 15:30 (market close buffer)

## Configuration

Edit `config.py` to change:

**Execution Times & Lot Sizes**
```python
STRATEGIES = [
    StrategyConfig("S0920", "09:20:00", 8, 20.0, 16000.0),
    #               name    time        lots  sl%   strategy_sl
]
```

**Portfolio Risk Limit**
```python
PORTFOLIO_SL_LIMIT = -80000.0  # Stop at -80k loss
```

**Indices** (currently NIFTY and SENSEX only)
```python
INDEX_CONFIGS = {
    "NIFTY": NIFTY,
    "SENSEX": SENSEX,
}
```

## Key Performance Indicators

- **MTM Updates**: Every 3 seconds (real-time dashboard)
- **Strategy Execution**: Once per day at scheduled time
- **API Calls**: ~10-15 per minute (lightweight)
- **Memory Usage**: ~50-100 MB
- **CPU Usage**: <5%

## Troubleshooting

**Bot won't start?**
```bash
# Check Python syntax
python3 -m py_compile bot.py

# Check logs
tail -f bot.log

# Verify API key is set
echo $XTS_API_KEY_5P
```

**No orders executing?**
- Confirm current time matches strategy execution time
- Check that spot LTP is being fetched (dashboard should show Spot price)
- Verify XTS API credentials are correct
- Review bot.log for API errors

**Web UI not accessible?**
```bash
# Check if Flask is running
ps aux | grep bot.py

# Test the API endpoint
curl http://localhost:8001/state
```

## Production Deployment on EC2

See [DEPLOYMENT.md](DEPLOYMENT.md) for full EC2 setup guide.

Quick summary:
1. Copy xts-bot-lite folder to `/opt/xts-bot` on EC2
2. Install dependencies: `pip install -r requirements.txt`
3. Create systemd service (see DEPLOYMENT.md)
4. Start service: `sudo systemctl start xts-bot`
5. Monitor: `sudo journalctl -u xts-bot -f`

## Support

- 📖 Full docs: [README.md](README.md)
- 🚀 EC2 deployment: [DEPLOYMENT.md](DEPLOYMENT.md)
- 📋 Project details: [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md)
- 🐛 Found a bug? Review bot.log and README troubleshooting

---

**Enjoy your simplified, production-ready XTS trading bot!** 🚀
