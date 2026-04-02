#!/bin/bash
# Aion Launch Script
# Prompts for production or dev mode, then starts the server.

AION_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$AION_DIR/aion/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "Error: Python not found at $PYTHON"
    echo "Make sure the venv exists at $AION_DIR/aion/"
    exit 1
fi

cd "$AION_DIR"

echo "=============================="
echo "  AION SERVER"
echo "=============================="
echo ""
echo "  1) Production"
echo "  2) Dev Mode"
echo ""
read -p "  Select mode [1]: " choice

case "$choice" in
    2)
        echo ""
        echo "  Starting in DEV MODE..."
        echo "  Production data will NOT be modified."
        echo ""
        "$PYTHON" server.py --dev
        ;;
    *)
        echo ""
        echo "  Starting in PRODUCTION..."
        echo ""
        "$PYTHON" server.py
        ;;
esac
