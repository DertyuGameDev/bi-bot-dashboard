FROM node:24-bookworm-slim AS codex
ARG CODEX_VERSION=0.165.0
RUN npm install --global --no-audit --no-fund "@openai/codex@${CODEX_VERSION}"

FROM python:3.13-slim-bookworm AS app
ARG UV_VERSION=0.11.28
LABEL org.opencontainers.image.title="BI-бот дашборд" \
      org.opencontainers.image.description="Чат для анализа данных и интерактивных BI-дашбордов"
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates fonts-dejavu-core git libstdc++6 libatomic1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "uv==${UV_VERSION}"
COPY --from=codex /usr/local/bin/node /usr/local/bin/node
COPY --from=codex /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && useradd --create-home --uid 10001 bi \
    && mkdir -p /app /data /codex /cache \
    && chown -R bi:bi /app /data /codex /cache
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:${PATH}" \
    HOME=/home/bi \
    CODEX_HOME=/codex \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    RPA_HOST=0.0.0.0 \
    RPA_PORT=8080 \
    RPA_STORAGE=/data/state.sqlite3
COPY --chown=bi:bi pyproject.toml uv.lock ./
USER bi
RUN uv sync --locked --no-dev --no-install-project
COPY --chown=bi:bi MY ./MY
COPY --chown=bi:bi shared ./shared
COPY --chown=bi:bi scripts ./scripts
COPY --chown=bi:bi docker ./docker
RUN codex --version
EXPOSE 8080 8082
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "-m", "MY"]
