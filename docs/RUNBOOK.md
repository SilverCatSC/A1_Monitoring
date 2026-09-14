# Runbook A1 Search Monitor

Краткий технический регламент. Пользовательский процесс — в `INSTRUCTION.md`.

## 1. Проверка состояния

```bash
docker-compose ps
curl -fsS http://127.0.0.1:${APP_BIND_PORT:-8000}/api/v1/health
curl -fsS http://127.0.0.1:${APP_BIND_PORT:-8000}/api/v1/ready
curl -fsS http://127.0.0.1:${APP_BIND_PORT:-8000}/api/v1/system/status
curl -fsS 'http://127.0.0.1:${APP_BIND_PORT:-8000}/api/v1/status/cycles?limit=20'
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
./scripts/deploy.sh --preflight
./scripts/deploy.sh --apply  # only after explicit owner approval and clean worktree
./scripts/smoke_stage.sh
```

Smoke не вызывает `/scan`. До M7 запускайте только host-only preflight:

```bash
./scripts/run_monitoring_host_macos.sh --preflight
```

После Gate 0/1 M7 и owner approval ровно один live controlled cycle выполняется
только через host runner:

```bash
./scripts/run_monitoring_host_macos.sh --engines auto_ru,avito --pages 3
curl -fsS http://127.0.0.1:${APP_BIND_PORT:-8000}/api/v1/status/scans/latest
curl -fsS http://127.0.0.1:${APP_BIND_PORT:-8000}/api/v1/status/scans/progress
```

`local_scan.sh`, `local_scan.py --watch` и `make watch` намеренно отказываются:
они не являются service scheduler или MacBook production entrypoint.
`NETWORK_PROFILE` маркирует происхождение запуска,
но не доказывает VPN-маршрут: до live cycle подтвердить VPSUS split-tunnel в
обычном Chrome, не выключая VPN или ChatGPT.

После фактической owner/browser проверки и до full host cycle нужен свежий
private record из [VPN admission contract](production/VPN_ADMISSION_CONTRACT_2026-09-14.md).
Он не создаётся скриптом, не содержит URL/cookies/настройки VPSUS, требует 0700/0600
и истекает максимум через 24 часа. Missing/expired/invalid record — stop-rule;
не подменять его фиктивным JSON и не менять VPN ради прохождения runner.
Кроме attestation central cycle требует проверяемый inherited Mac host-lock FD от
этого runner: boolean `LOCAL_BROWSER_HOST_ADMISSION` или файл сами по себе не
достаточны. Это защита от случайного прямого/container запуска, не hostile-security
утверждение против того же GUI-пользователя.

Startup сначала выполняет `alembic upgrade head`, затем запускает web.
`SCHEDULER_ENABLED=true` отвергается во всех окружениях; допускается только
`false`, поскольку контейнер не является решением для видимого host Chrome на
основном MacBook. Будущее расписание допускается только через один
per-user GUI LaunchAgent и `run_monitoring_host_macos.sh`, а не container scheduler
или вложенный `--watch`. До VPSUS proof, ручного MacBook cycle и отдельного owner
review этот LaunchAgent остаётся plan-only; детали —
[MacBook primary-host decision](production/MACBOOK_PRIMARY_HOST_2026-09-14.md).

Перед плановой регистрацией можно выполнить host-only preflight:

```bash
./scripts/run_monitoring_host_macos.sh --preflight
```

Он проверяет незаблокированную GUI-консоль текущего пользователя, Docker
`app`/`db`/`backup` и локальный readiness. Он не создаёт cycle, не открывает
Chrome, не посещает Auto.ru/Avito, не изменяет VPN и не доказывает TCC browser
control. Успех — диагностическое evidence готовности host, а не разрешение на
scan, `--apply` LaunchAgent или M7 acceptance.

## 3. Ручные операции

```bash
docker-compose exec -T app python -m app.cli import-source
./scripts/run_monitoring_host_macos.sh --engines auto_ru,avito --pages 3
./scripts/backup_now.sh
./scripts/restore_test.sh
```

Не запускать второй scan/discovery, если первый ещё выполняется: API вернёт 409.
Текущий прогресс также хранится в `artifacts/evidence/scan_progress.json`; запись
атомарная, dashboard опрашивает endpoint раз в секунду. После перезапуска worker
файл перезаписывается новым циклом.

`/status/cycles` — очередь полных попыток: разбирайте `partial_reasons` и `error`
даже если `/status/scans/latest` уже показывает более старый успешный run. Для
конкретной попытки используйте `/status/cycles/<cycle_id>` и
`/status/scans/latest?cycle_id=<cycle_id>`; нельзя собирать отчёт из разных ID.

Если все фильтры мгновенно завершаются ошибкой
`Browser.setDownloadBehavior ... not supported`, проверить `/json/list` Chrome на
порту 19222. Worker v0.6 сам создаёт `about:blank`, когда управляемых вкладок нет.

Локальный worker v0.6.2 по умолчанию использует `--pace cautious`: паузы
12–20 секунд между фильтрами и 6–12 секунд перед страницами. Dashboard показывает
события ожидания до начала паузы. `--pace normal` применять только для короткой
диагностики, понимая повышенную интенсивность запросов.

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

1. Проверить, что запуск имеет `network_profile=local_browser`.
2. Не переводить событие в absence вручную.
3. Открыть screenshot evidence без cookie/token.
4. Проверить публичную страницу вручную с тем же egress.
5. Обновить parser на сохранённой fixture.
6. Прогнать suite и один контрольный фильтр.

Результат с непроверенным маршрутом нельзя экстраполировать на production.
Сравнивать нужно через тот же подтверждённый split-tunnel и browser egress;
не отключать VPSUS или ChatGPT ради получения «чистого» результата.

### Нулевые результаты по всем фильтрам

Считать техническим инцидентом, пока ручная проверка не подтвердит реальную
пустую выдачу. Проверить URL фильтра, редирект, страницу авторизации и карточки DOM.

## 7. Инцидент: просроченный цикл

1. Проверить `docker-compose ps` и uptime app.
2. Проверить последние `ScanRun` и `SourceImportSnapshot`.
3. Убедиться, что локальный Chrome-профиль открыт и предыдущий worker не завис.
4. Проверить свободное место и DNS/HTTPS к Google Sheets.
5. После устранения и повторного прохождения gates выполнить один Mac host cycle
   либо `make retry-cycle CYCLE_ID=<UUID>` для terminal partial/failed; сверить status.

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
- рабочий запуск не имеет `NETWORK_PROFILE=local_browser` или свежей валидной
  VPN admission attestation; profiles `cloud_no_vpn`, `local_no_vpn` и
  `local_vpn` не могут создавать marketplace cycle;
- домен не имеет валидного HTTPS;
- PostgreSQL опубликован не на loopback;
- в git/логах обнаружен секрет;
- не отозваны старые GitHub/DeepSeek credentials;
- Drive-файл остаётся публично доступным на запись;
- последний backup не прошёл restore-test.
