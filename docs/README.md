# Documentation

Complete documentation for XTS Bot Lite. Start with [QUICKSTART.md](QUICKSTART.md) or [ARCHITECTURE.md](ARCHITECTURE.md) depending on your needs.

## Quick Navigation

### Getting Started
- 📖 **[QUICKSTART.md](QUICKSTART.md)** - 60-second setup guide
- 🚀 **[DEPLOYMENT.md](DEPLOYMENT.md)** - EC2 production deployment

### Understanding the System
- 🏗️ **[ARCHITECTURE.md](ARCHITECTURE.md)** - System design, modules, data flow, code simplifications
- 🧪 **[../tests/README.md](../tests/README.md)** - Testing & validation
- 🔧 **[../tests/TEST_COVERAGE_REPORT.md](../tests/TEST_COVERAGE_REPORT.md)** - Code coverage details

### Features
- 📋 **[features/STRATEGY_CLOSURE.md](features/STRATEGY_CLOSURE.md)** - Strategy SL closing mechanism
- 📊 **[features/ORDER_BOOK_MONITORING.md](features/ORDER_BOOK_MONITORING.md)** - Order book monitoring implementation

### Troubleshooting
- 🔌 **[ERROR_HANDLING.md](ERROR_HANDLING.md)** - Handling missing expiry data and API errors
- 🔧 **[fixes/EXCHANGE_SEGMENT_FIX.md](fixes/EXCHANGE_SEGMENT_FIX.md)** - Exchange segment configuration
- ⚙️ **[fixes/API_PARAMETER_FIX.md](fixes/API_PARAMETER_FIX.md)** - API parameter ordering

---

## Documentation Structure

```
docs/
├── README.md (this file)           # Documentation index
├── QUICKSTART.md                   # 60-second setup
├── DEPLOYMENT.md                   # Production EC2 setup
├── ARCHITECTURE.md                 # System design (consolidated)
├── ERROR_HANDLING.md               # Error handling & recovery
├── features/                       # Feature documentation
│   ├── STRATEGY_CLOSURE.md         # How strategy closing works
│   └── ORDER_BOOK_MONITORING.md    # Order book monitoring implementation
└── fixes/                          # Technical fix documentation
    ├── EXCHANGE_SEGMENT_FIX.md
    └── API_PARAMETER_FIX.md
```

---

## Reading Guide

### For New Users
1. Start with [QUICKSTART.md](QUICKSTART.md) - Get running in 60 seconds
2. Read [ARCHITECTURE.md](ARCHITECTURE.md) - Understand how it works
3. Run tests: `cd ../tests && ./preflight-check.sh`

### For Deployment
1. Read [DEPLOYMENT.md](DEPLOYMENT.md) - Set up on EC2
2. Run [../tests/preflight-check.sh](../tests/preflight-check.sh) - Pre-flight checks

### For Development
1. Read [ARCHITECTURE.md](ARCHITECTURE.md) - Understand design
2. Check [features/](features/) - Deep dive into specific features
3. Review [../tests/](../tests/) - See how features are tested

### For Understanding Specific Features
- **Strategy Closing:** [features/STRATEGY_CLOSURE.md](features/STRATEGY_CLOSURE.md)
- **Order Book Monitoring:** [features/ORDER_BOOK_MONITORING.md](features/ORDER_BOOK_MONITORING.md)
- **Error Handling:** [ERROR_HANDLING.md](ERROR_HANDLING.md)
- **Exchange Issues:** [fixes/EXCHANGE_SEGMENT_FIX.md](fixes/EXCHANGE_SEGMENT_FIX.md)
- **API Issues:** [fixes/API_PARAMETER_FIX.md](fixes/API_PARAMETER_FIX.md)

---

## Related Files
- **Main README:** [../README.md](../README.md) - Project overview
- **Configuration:** [../config.py](../config.py) - Bot settings
- **Bot Source:** [../bot.py](../bot.py) - Main application
- **Tests:** [../tests/](../tests/) - Unit and integration tests
