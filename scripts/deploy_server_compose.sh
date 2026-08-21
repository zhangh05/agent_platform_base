#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${LZCORE_SERVER_COMPOSE_FILE:-$ROOT/deployment/compose.server.yml}"

cd "$ROOT"
docker compose -f "$COMPOSE_FILE" config --quiet
docker compose -f "$COMPOSE_FILE" up -d --build --remove-orphans --force-recreate

backend_container="$(docker compose -f "$COMPOSE_FILE" ps -q backend)"
worker_container="$(docker compose -f "$COMPOSE_FILE" ps -q worker)"
frontend_container="$(docker compose -f "$COMPOSE_FILE" ps -q frontend)"
if [[ -z "$backend_container" || -z "$worker_container" || -z "$frontend_container" ]]; then
  echo "deployment_incomplete: expected backend, worker and frontend containers" >&2
  exit 1
fi

backend_image="$(docker inspect --format '{{.Image}}' "$backend_container")"
worker_image="$(docker inspect --format '{{.Image}}' "$worker_container")"
if [[ "$backend_image" != "$worker_image" ]]; then
  echo "deployment_version_mismatch: backend and worker images differ" >&2
  exit 1
fi

backend_port="${LZCORE_BACKEND_PORT:-8011}"
http_port="${LZCORE_HTTP_PORT:-5273}"
curl --fail --silent --show-error --retry 12 --retry-delay 2 \
  "http://127.0.0.1:${backend_port}/api/ready" >/dev/null
curl --fail --silent --show-error --retry 12 --retry-delay 2 \
  "http://127.0.0.1:${http_port}/api/ready" >/dev/null

echo "deployment_ok backend_worker_image=$backend_image"
