#!/usr/bin/env bash
set -euo pipefail

set -a
if [[ -f .env ]]; then
  source .env
fi
set +a

if command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
elif command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
else
  echo "Compose command not found. Install docker-compose or Docker Desktop/Colima with Compose." >&2
  exit 1
fi

if [[ "$COMPOSE_CMD" == "docker-compose" ]]; then
  "$COMPOSE_CMD" version >/dev/null 2>&1 || {
    echo "Docker daemon is not reachable for docker-compose." >&2
    exit 1
  }
  "$COMPOSE_CMD" ps >/dev/null 2>&1 || {
    echo "Docker daemon/socket not reachable for docker-compose (Colima/Docker Desktop). Start docker daemon and retry." >&2
    exit 1
  }
else
  docker compose version >/dev/null 2>&1 || {
    echo "Docker CLI plugin is not available." >&2
    exit 1
  }
  docker compose ps >/dev/null 2>&1 || {
    echo "Docker daemon/socket not reachable for docker compose." >&2
    exit 1
  }
fi

echo "Using compose command: $COMPOSE_CMD"
$COMPOSE_CMD build
$COMPOSE_CMD up -d

APP_BIND_PORT="${APP_BIND_PORT:-8000}"
BASE_URL="http://127.0.0.1:${APP_BIND_PORT}/api/v1"
for attempt in $(seq 1 30); do
  if curl --fail --silent "${BASE_URL}/health" >/dev/null \
    && curl --fail --silent "${BASE_URL}/ready" >/dev/null; then
    echo "Deploy verified: ${BASE_URL}/health"
    exit 0
  fi
  sleep 2
done

echo "Deploy failed readiness gate. Container status:" >&2
$COMPOSE_CMD ps >&2
$COMPOSE_CMD logs --tail=100 app >&2
exit 1
