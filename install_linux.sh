#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "Created .env. Edit it before starting the bot."
fi
mkdir -p data
chmod 700 data
printf '\nInstalled. Start with: .venv/bin/python -u bot.py\n'
