#!/usr/bin/env bash
# One-time setup for macOS / Linux:  ./setup.sh
set -e
cd "$(dirname "$0")"

PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ]; then
  echo "Python 3.10+ not found. Install it from https://www.python.org/downloads/" >&2
  exit 1
fi
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "Python 3.10 or newer is required (found: $("$PY" --version))." >&2; exit 1; }

echo "Installing packages..."
"$PY" -m pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "Created .env -- open it and fill in the broker you use."
else
  echo ".env already exists -- left as is."
fi

chmod +x run.sh
echo
echo "Setup complete. Run a scan with:  ./run.sh"
