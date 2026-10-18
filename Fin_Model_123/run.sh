#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"

if [ -d "$SCRIPT_DIR/Fin_Model_123" ]; then
    APP_DIR="$SCRIPT_DIR/Fin_Model_123"
fi

cd "$APP_DIR"

if [ -f ".venv/bin/streamlit" ]; then
    echo "Starting FinGraph AI via virtual environment..."
    PYTHONPATH=. .venv/bin/streamlit run final.py "$@"
elif command -v streamlit &> /dev/null; then
    echo "Starting FinGraph AI via streamlit..."
    PYTHONPATH=. streamlit run final.py "$@"
else
    source .venv/bin/activate
    PYTHONPATH=. streamlit run final.py "$@"
fi
