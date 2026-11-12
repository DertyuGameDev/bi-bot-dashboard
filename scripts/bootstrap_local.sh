#!/bin/sh
# Fresh checkout bootstrap. Uses only the published competition fixture.
set -eu
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo 'Install uv: https://docs.astral.sh/uv/'; exit 1; }
uv sync --locked
mkdir -p .local/pgsocket
if [ ! -f .local/puzzle-data.zip ]; then
  curl -fL --max-time 120 'https://nc.puzzle-rpa.ru/public.php/dav/files/gZaaDqjx2rcyfP4/?accept=zip' -o .local/puzzle-data.zip
fi
PG_BIN="${RPA_PG_BIN:-/opt/homebrew/opt/postgresql@16/bin}"
if [ ! -x "$PG_BIN/initdb" ]; then
  echo 'Set RPA_PG_BIN to the PostgreSQL 16 bin directory.'; exit 1
fi
if [ ! -d .local/pgdata ]; then
  "$PG_BIN/initdb" -D .local/pgdata -A trust --no-locale -E UTF8 > .local/initdb.log
fi
if ! "$PG_BIN/pg_ctl" -D .local/pgdata status >/dev/null 2>&1; then
  "$PG_BIN/pg_ctl" -D .local/pgdata -l .local/postgres.log -o "-p 55439 -h 127.0.0.1 -k $(pwd)/.local/pgsocket" start
fi
if [ ! -f .env ]; then
  uv run python scripts/import_data.py
else
  echo '.env exists: data and settings are preserved.'
fi
printf '\nReady: ./scripts/start_local.sh\n'
