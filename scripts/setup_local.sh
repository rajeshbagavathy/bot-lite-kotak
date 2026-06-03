#!/usr/bin/env bash
# Create .venv and install dependencies for local Kotak (or XTS) runs.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required." >&2
  exit 1
fi

python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

echo ""
echo "Done. Next:"
echo "  cp .env.example .env"
echo "  # Edit .env with your Kotak credentials"
echo "  ./scripts/run_bot_local.sh"
echo ""
