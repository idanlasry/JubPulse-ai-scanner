#!/usr/bin/env bash
# local/run_pipeline.sh — local pipeline scheduler.
#
# Mirrors GitHub Actions schedule: Mon-Fri at 08:00, 14:00, 18:00 Israel time.
#
# Usage:
#   bash local/run_pipeline.sh          # loop mode (keeps running)
#   bash local/run_pipeline.sh --once   # run once immediately and exit
#
# Background:
#   nohup bash local/run_pipeline.sh > logs/local.log 2>&1 &

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p logs

run_once() {
    echo "[scheduler] $(TZ='Asia/Jerusalem' date '+%Y-%m-%d %H:%M:%S %Z') — run started"
    uv run python local/run_local.py
    echo "[scheduler] $(TZ='Asia/Jerusalem' date '+%Y-%m-%d %H:%M:%S %Z') — run complete"
}

if [[ "${1:-}" == "--once" ]]; then
    run_once
    exit 0
fi

echo "[scheduler] Local JobPulse scheduler started"
echo "[scheduler] Triggers: Mon-Fri at 08:00, 14:00, 18:00 Israel time"
echo "[scheduler] Press Ctrl+C to stop"

while true; do
    HOUR=$(TZ="Asia/Jerusalem" date +%H)
    MIN=$(TZ="Asia/Jerusalem"  date +%M)
    DOW=$(TZ="Asia/Jerusalem"  date +%u)   # 1=Mon … 7=Sun

    if [[ "$DOW" -le 5 && "$MIN" == "00" ]] && \
       [[ "$HOUR" == "08" || "$HOUR" == "14" || "$HOUR" == "18" ]]; then
        run_once || echo "[scheduler] Run failed — will retry next trigger"
        sleep 61  # skip re-trigger within the same minute
    fi

    sleep 30
done
