#!/bin/bash
# Double-click me once. Creates a private Python environment for this project
# (.venv) and installs the libraries, then tests the connection to each site.
cd "$(dirname "$0")"
echo "Setting up NCAAM pipeline in: $(pwd)"
if ! command -v python3 >/dev/null; then
  echo "Python 3 not found. Install it from https://www.python.org/downloads/ and re-run."; read -n1; exit 1
fi
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip -q && pip install -r requirements.txt -q
echo; echo "Running offline tests…"; python -m pytest tests -q
echo; echo "Testing live connections…"; python run.py --diagnose
echo; echo "Done. Press any key to close."; read -n1
