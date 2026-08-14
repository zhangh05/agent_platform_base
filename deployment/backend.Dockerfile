FROM docker:29-cli AS docker-cli
FROM ghcr.io/astral-sh/uv:0.8.14 AS uv

FROM python:3.12-slim AS runtime

ARG PYTHON_PACKAGE_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LZCORE_RUNTIME_BIND_HOST=0.0.0.0 \
    LZCORE_WORKSPACE_ROOT=/var/lib/lzcore/workspaces

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=uv /uv /usr/local/bin/uv

RUN groupadd --system --gid 10001 lzcore \
    && useradd --system --uid 10001 --gid lzcore --home-dir /app lzcore \
    && mkdir -p /app /var/lib/lzcore/workspaces \
    && chown -R lzcore:lzcore /app /var/lib/lzcore

WORKDIR /app
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --index-url "${PYTHON_PACKAGE_INDEX_URL}" -r requirements.txt
COPY --chown=lzcore:lzcore . .

USER lzcore
EXPOSE 8011

CMD ["gunicorn", "--bind", "0.0.0.0:8011", "--workers", "1", "--threads", "16", "--timeout", "0", "--access-logfile", "-", "--error-logfile", "-", "backend.main:create_app()"]
