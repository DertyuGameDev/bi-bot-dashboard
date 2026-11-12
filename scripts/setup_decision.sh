#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
runtime="$(pwd)/.local/vendor/kev"
revision=2074cc44256200bb37540103fa4572901faa10f3
if [ ! -d "$runtime/.git" ]; then
  git -c core.hooksPath=/dev/null clone --depth 1 https://github.com/jaredpalmer/kev.git "$runtime"
fi
if [ "$(git -C "$runtime" rev-parse HEAD)" != "$revision" ]; then
  git -C "$runtime" fetch origin "$revision"
  git -C "$runtime" -c core.hooksPath=/dev/null checkout --detach "$revision"
fi
cd "$runtime"
uv sync --locked --no-dev --extra serve
echo 'Готово. Запуск: ./scripts/start_decision.sh (первая загрузка весов около 1,8 ГБ).'
