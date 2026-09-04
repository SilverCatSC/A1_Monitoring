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
echo "Deploy started. Check: http://localhost:8000/api/v1/health"
