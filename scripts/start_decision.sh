#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
runtime="$(pwd)/.local/vendor/kev"
if [ ! -x "$runtime/.venv/bin/python" ]; then
  echo 'Сначала выполните: ./scripts/setup_decision.sh' >&2
  exit 1
fi
cd "$runtime"
# v1.0 as measured; pin the resolved weights, not a movable Hub tag.
exec .venv/bin/python -m kev.serve --run jaredpalmer/kev-0.8b@bf75a6a8848ea6960ff2ed108d9ed44c2941174f --host 127.0.0.1 --port 8009
