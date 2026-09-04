#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

set -a
source .env
set +a

if [[ "${APP_ENV:-}" != "stage" ]]; then
  echo "This smoke test is intentionally limited to APP_ENV=stage." >&2
  exit 1
fi
if command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
elif docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
else
  echo "Compose command not found." >&2
  exit 1
fi

BASE_URL="http://127.0.0.1:${APP_BIND_PORT:-8000}/api/v1"
HEALTH_JSON="$(curl --fail --silent --show-error "$BASE_URL/health")"
READY_JSON="$(curl --fail --silent --show-error "$BASE_URL/ready")"
IMPORT_JSON="$(curl --fail --silent --show-error --request POST "$BASE_URL/import")"
STATUS_JSON="$(curl --fail --silent --show-error "$BASE_URL/system/status")"
DASHBOARD_HTML="$(curl --fail --silent --show-error "$BASE_URL/dashboard")"

HEALTH_JSON="$HEALTH_JSON" READY_JSON="$READY_JSON" IMPORT_JSON="$IMPORT_JSON" \
STATUS_JSON="$STATUS_JSON" python3 - <<'PY'
import json
import os

health = json.loads(os.environ['HEALTH_JSON'])
ready = json.loads(os.environ['READY_JSON'])
imported = json.loads(os.environ['IMPORT_JSON'])
status = json.loads(os.environ['STATUS_JSON'])
assert health['status'] == 'ok'
assert ready == {
    'status': 'ready',
    'database': 'ok',
    'environment': 'stage',
    'authentication': 'disabled',
}
assert imported['rows_total'] == 130
assert imported['rows_valid'] == 106
assert imported['rows_invalid'] == 24
assert status['import']['state'] == 'healthy'
assert {row['source'] for row in status['sources']} == {'auto_ru', 'avito'}
assert all(row['state'] == 'not_configured' for row in status['sources'])
print('HTTP_AND_SOURCE_OK')
PY

if [[ "$DASHBOARD_HTML" != *"Здоровье системы"* ]] \
  || [[ "$DASHBOARD_HTML" != *"Мониторинг ещё не настроен"* ]]; then
  echo "Dashboard does not expose required operational state." >&2
  exit 1
fi

APP_PORT_BINDING="$("${COMPOSE[@]}" port app 8000)"
DB_PORT_BINDING="$("${COMPOSE[@]}" port db 5432)"
if [[ "$APP_PORT_BINDING" != 127.0.0.1:* ]] || [[ "$DB_PORT_BINDING" != 127.0.0.1:* ]]; then
  echo "Stage ports are not loopback-only: app=$APP_PORT_BINDING db=$DB_PORT_BINDING" >&2
  exit 1
fi

MIGRATION="$("${COMPOSE[@]}" exec -T app alembic current)"
if [[ "$MIGRATION" != *"20260904_0004"* ]]; then
  echo "Unexpected migration state: $MIGRATION" >&2
  exit 1
fi

./scripts/backup_now.sh
./scripts/restore_test.sh

if "${COMPOSE[@]}" logs --tail=200 app | grep -q 'Traceback (most recent call last)'; then
  echo "Application log contains a traceback." >&2
  exit 1
fi

echo "STAGE_SMOKE_OK"
