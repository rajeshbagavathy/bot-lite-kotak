# Test Scripts

Validation and debugging test scripts for the XTS Bot.

## Quick Validation

```bash
# Pre-flight check (validates files, syntax, dependencies)
cd tests && ./preflight-check.sh

# Comprehensive config validation
python3 tests/test_all_config.py

# Unit tests for bot.py (99% coverage)
python3 -m pytest tests/test_bot.py -v
```

## Test Categories

### Unit Tests

**test_bot.py** - Comprehensive unit tests for bot.py  
- **40 tests** covering all functions  
- **99% code coverage** (185/186 lines)  
- All branches and edge cases tested  
- Fast execution (<1 second)  

```bash
# Run unit tests
python3 -m pytest tests/test_bot.py -v

# Run with coverage report
python3 -m coverage run --source=bot -m pytest tests/test_bot.py -v
python3 -m coverage report --include="bot.py"

# Generate HTML coverage report
python3 -m coverage html --include="bot.py"
# Open htmlcov/index.html in browser
```

📊 See [TEST_COVERAGE_REPORT.md](TEST_COVERAGE_REPORT.md) for detailed coverage analysis.

### Validation Tests

Run these to verify configuration is correct:

**Note:** Run all Python tests from the project root directory.

### Configuration Validation
```bash
# From project root
python3 tests/test_all_config.py

# Individual component tests
python3 tests/test_segments.py      # Exchange segment configuration
python3 tests/test_lot_sizes.py     # Lot size verification
python3 tests/test_api_params.py    # API parameter ordering
```

## Debugging Tools

### Expiry Debug Test
```bash
# From project root
python3 tests/test_expiry_debug.py
```

Tests expiry data fetching from XTS API for both NIFTY and SENSEX. Useful for:
- Validating API credentials
- Checking API connectivity
- Debugging expiry fetch issues

**Note:** Requires valid XTS API credentials in environment variables or .zshrc

## Demo/Integration Tests

Run these to test real-world scenarios and strategy logic:

### Multi-Strategy Test
```bash
# Test concurrent strategies on same strike
python3 tests/demo_multi_strategy_test.py
```

Validates:
- Multiple strategies executing simultaneously
- Each strategy maintains isolated positions
- SL closure doesn't affect other strategies
- Position tracking across different entry times

### Order Book Monitoring Test
```bash
# Test SL order execution and position closure
python3 tests/demo_order_book_monitoring_test.py
```

Validates:
- SL order detection and execution
- Automatic position closure on SL fill
- Multi-strategy isolation (same instrument, different strategies)
- Error handling for rejected orders

## Test Descriptions

| Test File | Purpose | Type | Dependencies |
|-----------|---------|------|--------------|
| `test_bot.py` | **Unit tests for bot.py (40 tests, 99% coverage)** | Unit | pytest, coverage |
| `preflight-check.sh` | Pre-flight validation (files, syntax, dependencies) | Validation | bash |
| `test_all_config.py` | Comprehensive validation of all config parameters | Validation | None |
| `test_segments.py` | Validate exchange segment configuration | Validation | None |
| `test_lot_sizes.py` | Verify NIFTY/SENSEX lot sizes | Validation | None |
| `test_api_params.py` | Validate API parameter ordering | Validation | None |
| `test_expiry_debug.py` | Debug expiry API calls with real credentials | Debugging | XTS API access |
| `test_error_handling.py` | Error handling and graceful failures | Unit | pytest |
| `test_daily_cleanup.py` | Database cleanup on startup | Unit | pytest |
| `demo_multi_strategy_test.py` | Multi-strategy concurrent execution | Integration | None |
| `demo_order_book_monitoring_test.py` | SL order execution and closure | Integration | None |
| `TEST_COVERAGE_REPORT.md` | Detailed unit test coverage analysis | Report | - |

## Running All Tests

```bash
# Unit tests (recommended - fast and comprehensive)
python3 -m pytest tests/test_bot.py -v

# Configuration validation - from project root
python3 tests/test_all_config.py

# Full validation with API - from project root
python3 tests/test_expiry_debug.py  # Requires credentials

# Pre-flight check - from tests directory
cd tests && ./preflight-check.sh
```

## Expected Results

All validation tests should show:
```
✅ All validations passed!
```

If any test fails, check the error message and verify:
1. Configuration values in `config.py`
2. API credentials (for expiry debug test)
3. XTS API endpoint in `config.ini`
