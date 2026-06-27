#!/usr/bin/env bash
# Market Radar Desktop — run script
# Priority: .env > ~/.bashrc TG_* exports > .env.example bootstrap

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load .env file if it exists (highest priority)
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

# If TG_* vars are still missing, try ~/.bashrc with login-shell semantics
# (bash -lc forces ~/.bash_profile / ~/.profile to load, but ~/.bashrc is
# typically interactive-only). We source it directly — if the user has the
# standard interactive-only guard at the top of ~/.bashrc, that guard will
# `return` early and TG_* won't appear. In that case, fall back to extracting
# them from the live shell environment via the login-shell variant.
NEED_RC=0
for var in TG_API_ID TG_API_HASH TG_PHONE TG_LLM_BASE_URL; do
    if [ -z "${!var:-}" ]; then
        NEED_RC=1
        break
    fi
done
if [ "$NEED_RC" = "1" ]; then
    # Use `bash -lic` which loads /etc/profile, ~/.bash_profile, ~/.profile,
    # and runs ~/.bashrc in interactive mode. Then export TG_* vars.
    while IFS='=' read -r key value; do
        case "$key" in
            TG_*) export "$key=$value" ;;
        esac
    done < <(bash -lic 'env' 2>/dev/null | grep -E '^TG_')
fi

# If still missing, offer to bootstrap .env from .env.example
MISSING=()
for var in TG_API_ID TG_API_HASH TG_PHONE TG_LLM_BASE_URL; do
    if [ -z "${!var:-}" ]; then
        MISSING+=("$var")
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    if [ ! -f .env ] && [ -f .env.example ]; then
        echo "[run.sh] No .env found and TG_* vars are not exported in your shell."
        echo "[run.sh] Bootstrapping .env from .env.example — please edit it with your real values."
        cp .env.example .env
        echo "[run.sh] Created .env — open it in your editor, fill in TG_API_ID / TG_API_HASH / TG_PHONE,"
        echo "[run.sh] then run ./run.sh again."
        echo ""
        exit 1
    fi
    echo "[run.sh] ERROR: missing env vars: ${MISSING[*]}"
    echo "[run.sh] Either:"
    echo "[run.sh]   1) Edit .env in this directory, OR"
    echo "[run.sh]   2) export TG_* in ~/.bashrc / ~/.profile (export, not just variable)"
    echo "[run.sh] See README.md for details."
    exit 1
fi

# Pick python
PYTHON_BIN="${PYTHON_BIN:-python3}"

# Verify deps
if ! "$PYTHON_BIN" -c "import PySide6, telethon, httpx, yfinance" 2>/dev/null; then
    echo "[run.sh] Missing dependencies. Installing..."
    "$PYTHON_BIN" -m pip install -r requirements.txt
fi

# Run (do NOT use exec — let the shell survive so error messages remain
# visible after the GUI closes; some desktop launchers kill the parent shell
# on exit which would swallow stderr).
"$PYTHON_BIN" run.py "$@"