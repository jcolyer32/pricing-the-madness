#!/bin/bash
# Double-click to refresh. Then in your model workbook: Data ▸ Refresh All.
cd "$(dirname "$0")"
source .venv/bin/activate 2>/dev/null || { echo "Run 'Setup (run once).command' first."; read -n1; exit 1; }
python run.py "$@"
code=$?
echo
if [ $code -eq 0 ]; then echo "✔ All sources refreshed."; else echo "⚠ Some sources failed — see the _Status sheet (last good data was kept)."; fi
echo "Press any key to close."; read -n1
