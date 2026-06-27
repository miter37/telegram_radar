#!/usr/bin/env bash
# Market Radar Desktop — run script
# Loads .env if present, exports TG_* variables, then launches the app.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load .env file if it exists (overriding nothing)
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

# Check required environment variables (warn only — first launch needs login)
MISSING=()
for var in TG_API_ID TG_API_HASH TG_PHONE TG_LLM_BASE_URL; do
    if [ -z "${!var:-}" ]; then
        MISSING+=("$var")
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "[run.sh] WARNING: missing env vars: ${MISSING[*]}"
    echo "[run.sh] The app will fail to connect without them."
    echo "[run.sh] See README.md for setup instructions."
    echo ""
fi

# Pick python
PYTHON_BIN="${PYTHON_BIN:-python3}"

# Verify deps
if ! "$PYTHON_BIN" -c "import PySide6, telethon, httpx, yfinance" 2>/dev/null; then
    echo "[run.sh] Missing dependencies. Installing..."
    "$PYTHON_BIN" -m pip install -r requirements.txt
fi

# Run
exec "$PYTHON_BIN" run.py "$@"