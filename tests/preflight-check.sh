#!/bin/bash
# XTS Bot Lite - Pre-flight Checklist

echo "=========================================="
echo "XTS Bot Lite - Pre-flight Checklist"
echo "=========================================="

cd "$(dirname "$0")"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Counters
PASSED=0
FAILED=0
WARNINGS=0

# Test functions
test_file() {
    if [ -f "$1" ]; then
        echo -e "${GREEN}✓${NC} File exists: $1"
        ((PASSED++))
    else
        echo -e "${RED}✗${NC} File missing: $1"
        ((FAILED++))
    fi
}

test_python_syntax() {
    if python3 -m py_compile "$1" 2>/dev/null; then
        echo -e "${GREEN}✓${NC} Python syntax OK: $1"
        ((PASSED++))
    else
        echo -e "${RED}✗${NC} Python syntax error: $1"
        ((FAILED++))
    fi
}

test_import() {
    if python3 -c "import $1" 2>/dev/null; then
        echo -e "${GREEN}✓${NC} Module available: $1"
        ((PASSED++))
    else
        echo -e "${YELLOW}⚠${NC} Module not installed: $1 (will install via pip)"
        ((WARNINGS++))
    fi
}

echo ""
echo "--- File Structure ---"
test_file "bot.py"
test_file "config.py"
test_file "xts_client.py"
test_file "mtm.py"
test_file "state.py"
test_file "ui.py"
test_file "Connect.py"
test_file "Exception.py"
test_file "config.ini"
test_file "requirements.txt"
test_file "README.md"
test_file "DEPLOYMENT.md"
test_file ".gitignore"

echo ""
echo "--- Python Syntax ---"
test_python_syntax "bot.py"
test_python_syntax "config.py"
test_python_syntax "xts_client.py"
test_python_syntax "mtm.py"
test_python_syntax "state.py"
test_python_syntax "ui.py"

echo ""
echo "--- Python Packages (Core) ---"
test_import "flask"
test_import "requests"
test_import "schedule"

echo ""
echo "--- Configuration Check ---"
if grep -q "root=https://xtsmum.5paisa.com/" config.ini; then
    echo -e "${GREEN}✓${NC} XTS API endpoint configured"
    ((PASSED++))
else
    echo -e "${YELLOW}⚠${NC} config.ini looks unusual, please verify"
    ((WARNINGS++))
fi

echo ""
echo "--- Credentials Check ---"
if [ -z "$XTS_API_KEY_5P" ]; then
    echo -e "${YELLOW}⚠${NC} XTS_API_KEY_5P env var not set (will use AWS SSM)"
    ((WARNINGS++))
else
    echo -e "${GREEN}✓${NC} XTS_API_KEY_5P env var set"
    ((PASSED++))
fi

echo ""
echo "=========================================="
echo "Results:"
echo -e "  ${GREEN}PASSED: $PASSED${NC}"
echo -e "  ${YELLOW}WARNINGS: $WARNINGS${NC}"
echo -e "  ${RED}FAILED: $FAILED${NC}"
echo "=========================================="

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ Ready to deploy!${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. pip install -r requirements.txt"
    echo "  2. Set credentials (env vars or AWS SSM)"
    echo "  3. python bot.py"
    echo "  4. Open http://localhost:8001 in browser"
    exit 0
else
    echo -e "${RED}✗ Fix the errors above before proceeding${NC}"
    exit 1
fi
