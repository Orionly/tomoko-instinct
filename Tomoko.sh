#!/bin/bash
cd "$(dirname "$0")"
mkdir -p logs
python3 -m uvicorn backend.api:app --host 127.0.0.1 --port 8000 --log-level warning &
sleep 3
open http://127.0.0.1:8000
echo "Tomoko is running at http://127.0.0.1:8000"
echo "CSV: logs/brain_signals.csv"
