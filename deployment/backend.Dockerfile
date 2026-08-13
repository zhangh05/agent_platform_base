FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENT_PLATFORM_RUNTIME_BIND_HOST=0.0.0.0 \
    NA_WORKSPACE_ROOT=/var/lib/agent-platform/workspaces

RUN apt-get update \
    && apt-get install --yes --no-install-recommends docker-cli \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 agent-platform \
    && useradd --system --uid 10001 --gid agent-platform --home-dir /app agent-platform \
    && mkdir -p /app /var/lib/agent-platform/workspaces \
    && chown -R agent-platform:agent-platform /app /var/lib/agent-platform

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt
COPY --chown=agent-platform:agent-platform . .

USER agent-platform
EXPOSE 8011

CMD ["gunicorn", "--bind", "0.0.0.0:8011", "--workers", "1", "--threads", "16", "--timeout", "0", "--access-logfile", "-", "--error-logfile", "-", "backend.main:create_app()"]
