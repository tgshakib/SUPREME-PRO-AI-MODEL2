#!/usr/bin/env bash
# SUPREME PRO AI BOT — universal launcher
# Works on: JustRunMy, Render, Railway, Fly.io, Heroku, any Linux VPS,
# and from a freshly-cloned GitHub repo.
set -euo pipefail

# Pick the right Python: prefer python3 if present
if command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  PY=python
fi

# Install deps if they're missing (first-run on a fresh host)
if ! "$PY" -c "import aiogram" >/dev/null 2>&1; then
  echo "[start.sh] Installing dependencies…"
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install -r requirements.txt
fi

# Sanity check the secret
if [ -z "${BOT_TOKEN:-}" ]; then
  echo "ERROR: BOT_TOKEN env var is not set."
  echo "Set it in your host's secrets / env vars panel and restart."
  exit 1
fi

exec "$PY" bot.py
