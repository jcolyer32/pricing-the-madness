#!/bin/bash
# Double-click to build/refresh the research dataset (output/ncaam_research.xlsx + gretl files).
cd "$(dirname "$0")"
source .venv/bin/activate 2>/dev/null || { echo "Run 'Setup (run once).command' first."; read -n1; exit 1; }
python run_research.py "$@"
echo; echo "Done — see output/ncaam_research.xlsx (_Status sheet first). Press any key to close."; read -n1
