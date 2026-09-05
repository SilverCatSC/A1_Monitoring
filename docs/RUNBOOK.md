# Runbook A1 Search Monitor

Краткий технический регламент. Пользовательский процесс — в `INSTRUCTION.md`.

## 1. Проверка состояния

```bash
docker-compose ps
curl -fsS http://127.0.0.1:${APP_BIND_PORT:-8000}/api/v1/health
curl -fsS http://127.0.0.1:${APP_BIND_PORT:-8000}/api/v1/ready
curl -fsS http://127.0.0.1:${APP_BIND_PORT:-8000}/api/v1/system/status
docker-compose logs --tail=200 app
```

`health=ok` означает только работающий процесс. Для бизнеса проверять
`system/status`: `not_configured`, `overdue` и `degraded` требуют действия.

## 2. Deploy

Перед каждым обновлением:

```bash
./scripts/backup_now.sh
./scripts/restore_test.sh
.venv312/bin/pytest -q
.venv312/bin/ruff check src tests
git diff --check
```

Локальный stage:

```bash
./scripts/deploy.sh
./scripts/smoke_stage.sh
```

Smoke не вызывает `/scan`. Для контролируемой живой приёмки после подтверждения
no-VPN маршрута:

```bash
./scripts/live_acceptance.sh
curl -fsS http://127.0.0.1:${APP_BIND_PORT:-8000}/api/v1/status/scans/latest
```

При `NETWORK_PROFILE=local_vpn|unknown`, неполном каталоге или отсутствии
ожиданий live-gate обязан завершиться `LIVE_ACCEPTANCE_BLOCKED` до POST `/scan`.
Сам API повторяет сетевой gate и возвращает HTTP 422 до создания `ScanRun`, если
клиент попытается вызвать `/scan` или `/cycle` напрямую.

Cloud:

```bash
docker-compose \
  -f docker-compose.yml \
  -f deploy/cloud/docker-compose.cloud.yml \
  up -d --build
```

Startup сначала выполняет `alembic upgrade head`, затем запускает web. Scheduler
запускается только при явном `SCHEDULER_ENABLED=true`; локальный stage использует
`false`.

## 3. Ручные операции

```bash
docker-compose exec -T app python -m app.cli import-source
docker-compose exec -T app python -m app.cli scan
docker-compose exec -T app python -m app.cli run-cycle
./scripts/backup_now.sh
./scripts/restore_test.sh
```

Не запускать второй scan/discovery, если первый ещё выполняется: API вернёт 409.

## 4. Диагностические запросы

```bash
docker-compose exec -T db psql -U monitor -d a1_search_monitor -c \
  "SELECT version_num FROM alembic_version"

docker-compose exec -T db psql -U monitor -d a1_search_monitor -c \
  "SELECT source, status, started_at, finished_at, technical_errors, notes
   FROM scan_runs ORDER BY started_at DESC LIMIT 20"

docker-compose exec -T db psql -U monitor -d a1_search_monitor -c \
  "SELECT started_at, valid_rows, invalid_rows, blocked_by_schema_drift
   FROM source_import_snapshots ORDER BY started_at DESC LIMIT 10"
```

Не выводить `.env`, cookie или browser storage в тикет/чат.

## 5. Инцидент: импорт

### Признаки

- HTTP 422 у `/import`;
- `quarantine` в operational status;
- резкое изменение valid/invalid rows.

### Действия

1. Не повторять импорт с ослабленным порогом.
2. Сравнить заголовки с `docs/DATA_CONTRACT.md`.
3. Зафиксировать реальное изменение подрядчика.
4. Добавить alias и regression test.
5. Повторить import; проверить, что last-good реестр не был обрезан.

## 6. Инцидент: площадка

### CAPTCHA / 429 / неизвестный DOM

1. Проверить `network_profile` и среду запуска.
2. Не переводить событие в absence вручную.
3. Открыть screenshot evidence без cookie/token.
4. Проверить публичную страницу вручную с тем же egress.
5. Обновить parser на сохранённой fixture.
6. Прогнать suite и один контрольный фильтр.

VPN-результат нельзя экстраполировать на production без VPN.

### Нулевые результаты по всем фильтрам

Считать техническим инцидентом, пока ручная проверка не подтвердит реальную
пустую выдачу. Проверить URL фильтра, редирект, страницу авторизации и карточки DOM.

## 7. Инцидент: просроченный цикл

1. Проверить `docker-compose ps` и uptime app.
2. Проверить последние `ScanRun` и `SourceImportSnapshot`.
3. Убедиться, что предыдущий Playwright процесс не завис.
4. Проверить свободное место и DNS/HTTPS к Google Sheets.
5. После устранения выполнить один `run-cycle` и сверить status.

## 8. Восстановление

Сначала проверить dump через `restore_test.sh`. Для реального восстановления:

1. остановить app, не удаляя volumes;
2. сохранить копию текущей БД;
3. восстановить выбранный проверенный dump в отдельную БД;
4. сравнить версии миграции и контрольные количества;
5. переключить app только после проверки Owner;
6. сохранить старую БД до окончания инцидента.

Не использовать `docker-compose down -v`, `DROP DATABASE` production или
destructive Alembic downgrade как штатный способ отката.

## 9. Security stop-rules

Остановить production deploy, если:

- `AUTH_ENABLED=false`;
- `NETWORK_PROFILE` не `cloud_no_vpn`;
- домен не имеет валидного HTTPS;
- PostgreSQL опубликован не на loopback;
- в git/логах обнаружен секрет;
- не отозваны старые GitHub/DeepSeek credentials;
- Drive-файл остаётся публично доступным на запись;
- последний backup не прошёл restore-test.
