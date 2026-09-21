#!/usr/bin/env bash
# macOS / Linux launcher.  ./run.sh [any scan.py options]
#   ./run.sh                          Definedge, all scanners, Daily + Weekly
#   ./run.sh --source kotak
#   ./run.sh --offline                cached data, no login
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python)
[ -z "$PY" ] && { echo "Python 3.10+ not found -- run ./setup.sh" >&2; exit 1; }
"$PY" -c "import pandas, numpy, requests, dotenv, pyotp" 2>/dev/null \
  || { echo "Packages missing -- run ./setup.sh first." >&2; exit 1; }

if [ $# -eq 0 ]; then
  set -- --source definedge --open
fi
exec "$PY" scan.py "$@"
