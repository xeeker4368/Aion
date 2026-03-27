#!/bin/bash
# Sets up daily backup cron job for Aion
# Run once: bash setup_cron.sh

AION_DIR="$HOME/aion"
PYTHON="$AION_DIR/aion/bin/python"

# Check if venv python exists
if [ ! -f "$PYTHON" ]; then
    echo "Error: Python not found at $PYTHON"
    exit 1
fi

# Add cron job: run backup daily at 1:00 AM
CRON_CMD="0 1 * * * cd $AION_DIR && $PYTHON backup.py >> $AION_DIR/data/logs/backup.log 2>&1"

# Check if already installed
if crontab -l 2>/dev/null | grep -q "aion.*backup.py"; then
    echo "Backup cron job already exists."
else
    (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -
    echo "Backup cron job installed: daily at 1:00 AM"
    echo "  $CRON_CMD"
fi

echo ""
echo "Current crontab:"
crontab -l
