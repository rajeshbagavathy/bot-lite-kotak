# Project Organization

This document describes the reorganized structure of xts-bot-lite.

## Directory Structure

```
xts-bot-lite/
├── README.md              # Main project documentation
├── bot.py                 # Main bot application
├── config.py              # Configuration
├── xts_client.py          # XTS API client
├── mtm.py                 # MTM calculations
├── state.py               # State management
├── ui.py                  # Web dashboard
├── Connect.py             # XTS SDK
├── Exception.py           # XTS exceptions
├── config.ini             # API endpoints
├── requirements.txt        # Dependencies
├── .gitignore             # Git ignore rules
│
├── docs/                  # 📚 Documentation
│   ├── README.md          # Documentation index
│   ├── QUICKSTART.md      # Quick start guide
│   ├── DEPLOYMENT.md      # EC2 deployment guide
│   ├── PROJECT_SUMMARY.md # Architecture overview
│   └── fixes/             # Technical fixes documentation
│       ├── EXCHANGE_SEGMENT_FIX.md
│       └── API_PARAMETER_FIX.md
│
└── tests/                 # 🧪 Testing & Validation
    ├── README.md          # Test documentation
    ├── preflight-check.sh # Pre-flight validation script
    ├── test_all_config.py # Comprehensive validation
    ├── test_segments.py   # Exchange segment tests
    ├── test_lot_sizes.py  # Lot size tests
    ├── test_api_params.py # API parameter tests
    └── test_expiry_debug.py # Expiry API debugging
```

## File Categories

### Core Application (Root)
- **bot.py** - Main application entry point
- **config.py** - Configuration and credentials
- **xts_client.py** - XTS API wrapper
- **mtm.py** - Mark-to-market calculations
- **state.py** - Thread-safe state management
- **ui.py** - Flask web dashboard
- **requirements.txt** - Python dependencies

### XTS SDK (Root)
- **Connect.py** - XTS Connect SDK (from original project)
- **Exception.py** - XTS exceptions (from original project)
- **config.ini** - XTS API endpoint configuration

### Documentation (docs/)
- **QUICKSTART.md** - Fast setup for development
- **DEPLOYMENT.md** - Production EC2 deployment guide
- **PROJECT_SUMMARY.md** - Architecture and design decisions
- **fixes/** - Technical fix documentation

### Testing & Validation (tests/)
- **preflight-check.sh** - Shell script for pre-flight checks
- **test_all_config.py** - Run this first for comprehensive validation
- **test_*.py** - Individual component validation tests
- **test_expiry_debug.py** - Debug tool for API connectivity

## Usage Patterns

### For New Users
1. Read [README.md](README.md) - Overview and installation
2. Follow [docs/QUICKSTART.md](docs/QUICKSTART.md) - Get running quickly
3. Run [tests/test_all_config.py](tests/test_all_config.py) - Validate setup

### For Deployment
1. Read [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) - EC2 production setup
2. Run [tests/preflight-check.sh](tests/preflight-check.sh) - Pre-deployment validation

### For Development
1. Read [docs/PROJECT_SUMMARY.md](docs/PROJECT_SUMMARY.md) - Understand architecture
2. Check [docs/fixes/](docs/fixes/) - Learn about technical decisions

### For Debugging
1. Run [tests/test_all_config.py](tests/test_all_config.py) - Validate configuration
2. Run [tests/test_expiry_debug.py](tests/test_expiry_debug.py) - Test API connectivity

## Changes from Previous Structure

### Moved to docs/
- ~~DEPLOYMENT.md~~ → docs/DEPLOYMENT.md
- ~~PROJECT_SUMMARY.md~~ → docs/PROJECT_SUMMARY.md
- ~~QUICKSTART.md~~ → docs/QUICKSTART.md
- ~~EXCHANGE_SEGMENT_FIX.md~~ → docs/fixes/EXCHANGE_SEGMENT_FIX.md
- ~~API_PARAMETER_FIX.md~~ → docs/fixes/API_PARAMETER_FIX.md

### Moved to tests/
- ~~test_*.py~~ → tests/test_*.py
- ~~preflight-check.sh~~ → tests/preflight-check.sh

### Cleaned Up
- ~~bot.log~~ - Removed (runtime log, now in .gitignore)
- ~~bot_test.log~~ - Removed (runtime log, now in .gitignore)

### Updated
- **.gitignore** - Added bot_test.log
- **README.md** - Updated with new structure references

## Benefits

✅ **Cleaner Root Directory** - Only essential application files  
✅ **Organized Documentation** - All docs in one place  
✅ **Isolated Test Scripts** - Testing separated from production code  
✅ **Better Navigation** - Clear hierarchy and purpose  
✅ **Easier Maintenance** - Logical grouping of related files  
✅ **Professional Structure** - Follows Python project conventions  

## Quick Commands

```bash
# Validate configuration
cd tests && python3 test_all_config.py

# View all documentation
ls docs/

# Run pre-flight checks
cd tests && ./preflight-check.sh

# Start the bot
python3 bot.py
```
