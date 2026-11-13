#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
case "${1:-}" in
  --configure-model) .venv/bin/python scripts/configure_model.py ;;
  '') ;;
  *) echo 'Usage: ./scripts/start_local.sh [--configure-model]'; exit 2 ;;
esac
if [ "${1:-}" = --configure-model ]; then
  .venv/bin/python scripts/check_model.py
fi
if [ -d .local/pgdata ]; then
  PG_BIN="${RPA_PG_BIN:-/opt/homebrew/opt/postgresql@16/bin}"
  if ! "$PG_BIN/pg_ctl" -D .local/pgdata status >/dev/null 2>&1; then
    "$PG_BIN/pg_ctl" -D .local/pgdata -l .local/postgres.log -o "-p 55439 -h 127.0.0.1 -k $(pwd)/.local/pgsocket" start
  fi
fi
exec .venv/bin/python -m MY
