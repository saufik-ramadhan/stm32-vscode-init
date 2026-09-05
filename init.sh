#!/usr/bin/env bash
# Thin wrapper so Linux/macOS users can run ./init.sh instead of invoking python3 directly.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "ERROR: $PYTHON_BIN not found. Install Python 3 (e.g. 'sudo apt install python3' / 'brew install python3')." >&2
    exit 1
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/stm32_vscode_init.py" "$@"
