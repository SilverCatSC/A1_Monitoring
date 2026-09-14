#!/usr/bin/env bash
set -euo pipefail

MODE="${1:---preflight}"
if [[ "$MODE" != "--preflight" && "$MODE" != "--apply" ]]; then
  echo "Usage: $0 [--preflight|--apply]" >&2
  exit 2
fi

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

if command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
elif command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
else
  echo "Compose command not found. Install docker-compose or Docker Desktop/Colima with Compose." >&2
  exit 1
fi

"${COMPOSE[@]}" version >/dev/null
"${COMPOSE[@]}" config -q
"${COMPOSE[@]}" ps >/dev/null

APP_PORT="18000"
if [[ -f .env ]]; then
  candidate="$(awk -F= '/^[[:space:]]*APP_BIND_PORT[[:space:]]*=/ { value=$2; gsub(/[[:space:]"'\'' ]/, "", value); print value; exit }' .env)"
  if [[ -n "$candidate" ]]; then
    APP_PORT="$candidate"
  fi
fi
if [[ ! "$APP_PORT" =~ ^[0-9]{1,5}$ ]] || (( APP_PORT < 1 || APP_PORT > 65535 )); then
  echo "APP_BIND_PORT is invalid." >&2
  exit 1
fi

PYTHON="$PROJECT_DIR/.venv312/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Expected Python 3.12 environment at .venv312 is missing." >&2
  exit 1
fi
TARGET_REVISION="$($PYTHON -m alembic heads | awk 'NR == 1 { print $1 }')"
if [[ -z "$TARGET_REVISION" ]]; then
  echo "Could not determine Alembic head revision from the checked-out source." >&2
  exit 1
fi

read_state() {
  CURRENT_REVISION="$("${COMPOSE[@]}" exec -T db psql -U monitor -d a1_search_monitor -Atc 'SELECT version_num FROM alembic_version')"
  RUNNING_VERSION="$("${COMPOSE[@]}" exec -T app python -c 'from app.config import settings; print(settings.app_version)')"
  printf 'DEPLOY_STATE app_version=%s db_revision=%s target_revision=%s\n' \
    "$RUNNING_VERSION" "$CURRENT_REVISION" "$TARGET_REVISION"
}

read_state
if [[ "$MODE" == "--preflight" ]]; then
  if [[ "$CURRENT_REVISION" == "$TARGET_REVISION" ]]; then
    echo "DEPLOY_PREFLIGHT_OK apply_not_requested=true"
    exit 0
  fi
  echo "DEPLOY_PREFLIGHT_BLOCKED migration_required=true apply_not_requested=true" >&2
  exit 3
fi

if [[ -n "$(git status --short)" ]]; then
  echo "Refusing deploy with a dirty worktree. Commit, stash, or remove unrelated changes first." >&2
  exit 1
fi

echo "DEPLOY_APPLY started=true compose=${COMPOSE[*]}"
"${COMPOSE[@]}" up -d --build app db backup

BASE_URL="http://127.0.0.1:${APP_PORT}/api/v1"
for attempt in $(seq 1 30); do
  if curl --fail --silent "${BASE_URL}/health" >/dev/null \
    && curl --fail --silent "${BASE_URL}/ready" >/dev/null; then
    read_state
    if [[ "$CURRENT_REVISION" != "$TARGET_REVISION" ]]; then
      echo "DEPLOY_FAILED migration_not_at_head=true" >&2
      exit 1
    fi
    echo "DEPLOY_OK health=${BASE_URL}/health"
    exit 0
  fi
  sleep 2
done

echo "DEPLOY_FAILED readiness_timeout=true" >&2
"${COMPOSE[@]}" ps >&2
"${COMPOSE[@]}" logs --tail=100 app >&2
exit 1
