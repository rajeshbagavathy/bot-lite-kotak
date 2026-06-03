#!/usr/bin/env bash
# Run the bot using the project virtualenv (after scripts/setup_local.sh).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv. Run: ./scripts/setup_local.sh" >&2
  exit 1
fi

exec .venv/bin/python bot.py "$@"
