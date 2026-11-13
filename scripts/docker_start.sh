#!/bin/sh
# Docker-only bootstrap: no Python/PostgreSQL/Node installation on the host.
set -eu
cd "$(dirname "$0")/.."
umask 077
command -v docker >/dev/null || { echo 'Install Docker with Compose: https://docs.docker.com/get-started/get-docker/'; exit 1; }
bi_configure=0
case "${1:-}" in
  --configure-model) bi_configure=1 ;;
  '') ;;
  *) echo 'Usage: ./scripts/docker_start.sh [--configure-model]'; exit 2 ;;
esac
docker compose version >/dev/null
docker info >/dev/null 2>&1 || { echo 'Start Docker Desktop, OrbStack, or Colima and retry.'; exit 1; }
bi_new_install=0
if [ ! -f .env.docker ]; then
  bi_new_install=1
  bi_admin_password="$(docker run --rm python:3.13-slim-bookworm python -c 'import secrets; print(secrets.token_hex(24))')"
  bi_reader_password="$(docker run --rm python:3.13-slim-bookworm python -c 'import secrets; print(secrets.token_hex(24))')"
  sed -e "s/^BI_DB_ADMIN_PASSWORD=.*/BI_DB_ADMIN_PASSWORD=$bi_admin_password/" \
      -e "s/^BI_DB_READER_PASSWORD=.*/BI_DB_READER_PASSWORD=$bi_reader_password/" \
      .env.docker.example > .env.docker
  echo 'Created private .env.docker with generated database passwords.'
fi
if [ "$bi_configure" = 1 ] || { [ "$bi_new_install" = 1 ] && [ -t 0 ] && [ -t 1 ] && [ -z "${BI_MODEL_ADAPTER:-}" ]; }; then
  if [ ! -t 0 ] || [ ! -t 1 ]; then
    echo 'Run --configure-model in an interactive terminal, or configure .env.docker directly.'; exit 2
  fi
  docker run --rm -it --user "$(id -u):$(id -g)" -v "$(pwd):/workspace" -w /workspace \
    python:3.13-slim-bookworm python scripts/configure_model.py --docker
fi
mkdir -p data
compose() { docker compose --env-file .env.docker "$@"; }
compose config --quiet
compose build app
bi_adapter="$(compose run --rm --no-deps -T app python -c 'from shared.config import Settings; print(Settings().model_adapter)')"
if [ "${bi_adapter:-codex}" = codex ]; then
  if ! compose run --rm --no-deps -T app codex login status >/dev/null 2>&1; then
    bi_auth_file="${CODEX_HOME:-$HOME/.codex}/auth.json"
    if [ -f "$bi_auth_file" ]; then
      compose run --rm --no-deps -T app sh -c 'cat > "$CODEX_HOME/auth.json"; chmod 600 "$CODEX_HOME/auth.json"' < "$bi_auth_file"
    fi
    if ! compose run --rm --no-deps -T app codex login status >/dev/null 2>&1; then
      compose run --rm --no-deps app codex login --device-auth
    fi
  fi
fi
compose run --rm --no-deps -T app python scripts/check_model.py
compose up -d --wait --wait-timeout 300
bi_port="${BI_PORT:-$(sed -n 's/^BI_PORT=//p' .env.docker | tail -n 1)}"
printf '\nBI-бот дашборд: http://127.0.0.1:%s\n' "${bi_port:-8080}"
echo 'Stop without deleting data: docker compose --env-file .env.docker down'
