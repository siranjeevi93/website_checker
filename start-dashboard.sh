#!/bin/bash
# Start the Website Monitor dashboard if it is not already running.
# Used by @reboot (boot persistence) and a periodic cron (crash recovery).
cd /home/virsec/website-monitor-mcp || exit 1
# Already up? (bracket trick so this check does not match itself).
# Pattern is dashboard.py — deliberately distinct from the ova-deployer
# webapp.py so neither watchdog ever matches the other's process.
if pgrep -f "[d]ashboard.py" >/dev/null; then
  exit 0
fi
setsid ./venv/bin/python dashboard.py >> dashboard.log 2>&1 < /dev/null &
