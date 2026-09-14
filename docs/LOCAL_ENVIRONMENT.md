# Локальное окружение

Целевой production-контур — MSI / Windows 11 / 16 ГБ, но его живой runtime ещё не
принят. Mac/stage является текущей проверенной технической средой в ограниченных
границах. [Основная инструкция Windows](WINDOWS_11_INSTALL.md).

## MSI / Windows 11

Полная актуальная инструкция: [Установка и запуск Windows 11](WINDOWS_11_INSTALL.md).
Используйте PowerShell 7 и согласованный commit из
[A1_Monitoring](https://github.com/SilverCatSC/A1_Monitoring). Новая установка не
переносит историю с Mac автоматически.

Основной рабочий компьютер — MSI с Windows 11, Intel Core Ultra 5 125U и 16 ГБ RAM.
Откройте PowerShell в корне репозитория:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd C:\work\A1_Monitoring
.\scripts\setup_windows.ps1
.\scripts\install_ai_tools_windows.ps1
.\scripts\download_local_model_windows.ps1
```

Нужны Docker Desktop с запущенным Linux containers/WSL2 backend, Git for Windows,
Python 3.12 и Google Chrome. Скрипт создаёт `.venv312`, устанавливает зафиксированные
зависимости, создаёт `.env` только при его отсутствии, запускает PostgreSQL/app/backup
и проверяет `/api/v1/ready`. Он не открывает площадки и не выполняет живой скан.

Запуск приложения и локального отчёта:

```powershell
.\scripts\start_windows.ps1 -OpenDashboard
```

Живой мониторинг выполняется после подтверждения VPSUS split-tunnel: VPN и
ChatGPT/Codex остаются включёнными, а Auto.ru/Avito открываются в отдельном Chrome
профиле по approved direct browser rules:

```powershell
.\scripts\local_scan_windows.ps1 -Engines auto_ru,avito -Pages 3 -Pace cautious
```

Полный последовательный цикл с освобождением памяти перед локальной моделью:

```powershell
.\scripts\run_full_monitoring_windows.ps1
```

Ручной foreground-повтор каждые шесть часов после завершения прошлого цикла:

```powershell
.\scripts\local_scan_windows.ps1 -Watch -IntervalMinutes 360 -Pace cautious
```

`--watch` не является Windows-службой: он требует открытого терминала и не может
работать параллельно с host-runner/Task Scheduler. Запускатель использует видимый
Chrome, `NETWORK_PROFILE=local_browser`, loopback CDP и отдельный
`artifacts/local_chrome_profile`. CAPTCHA остаётся ручным событием; 401/403/429,
таймаут и неизвестная выдача сохраняются как technical error.

После ручной MSI-приёмки и owner review production-расписание использует ровно один
`run_monitoring_host_windows.ps1` на trigger. План
`register_monitoring_task_windows.ps1 -At HH:mm` без `-Apply` только описывает
`\A1Monitoring\InteractiveCycle`; `-Apply` отдельно регистрирует интерактивную
задачу с mutex, `MultipleInstances=IgnoreNew` и `RestartCount=0`. Это M7 gate,
а не доказательство принятого Windows runtime.

Остановка сервисов без удаления данных:

```powershell
docker compose stop
```

Не используйте `docker compose down -v`: Docker volumes содержат историю и backup.

## Публикация HTML и ссылка Bitrix

**Публикация не прошла приёмку.** Команды ниже не являются разрешением
публиковать рабочие данные. Export создаёт только новую aggregate-only страницу
без VIN, URL, карточек, evidence и текстов замечаний, но её фактический HTML ещё
не прошёл owner review. Не используйте `-Push` без свежего owner allowlist и
ручной проверки результата.

После завершённого локального прогона создать owner allowlist **вне Git** и
экспортировать единственную публичную сводку в ignored `public/`:

```powershell
.\scripts\publish_pages.ps1 -Allowlist 'C:\secure\publication_allowlist.json'
```

По умолчанию это только экспорт. Явная отправка коммита в GitHub:

```powershell
.\scripts\publish_pages.ps1 -Allowlist 'C:\secure\publication_allowlist.json' -Push -CommitMessage 'Publish aggregate monitoring report'
```

Workflow `.github/workflows/pages.yml` разместит содержимое `public/` на GitHub Pages.
Перед `-Push` убедиться, что в папке есть только `index.html`, `static/` и
`PUBLICATION_MANIFEST.json`; export намеренно откажется от каталога с неизвестными
старыми файлами.
Репозиторий проекта: `SilverCatSC/A1_Monitoring`; ожидаемый адрес отчёта:
`https://silvercatsc.github.io/A1_Monitoring/`.

Входящий webhook Bitrix24 создаётся администратором один раз. Его полный адрес и
идентификатор диалога хранятся только в `.env` MSI:

```text
PUBLIC_REPORT_URL=https://silvercatsc.github.io/A1_Monitoring/
BITRIX_WEBHOOK_URL=https://portal.example.bitrix24.ru/rest/1/secret/im.message.add.json
BITRIX_DIALOG_ID=chat123
```

Сначала проверьте payload без отправки:

```powershell
.\scripts\bitrix_publish.ps1 -ReportUrl 'https://silvercatsc.github.io/A1_Monitoring/'
```

Отправка выполняется только с явным `-Send`:

```powershell
.\scripts\bitrix_publish.ps1 -ReportUrl 'https://silvercatsc.github.io/A1_Monitoring/' -Send
```

Webhook не выводится в консоль и не коммитится. Результат последнего запроса
сохраняется локально в `artifacts/bitrix_publish_last.json`. Скрипт не меняет
настройки Bitrix; он только отправляет сообщение в заранее выбранный диалог.

## Первый запуск

Установите Python 3.12, Google Chrome и Docker Desktop (или совместимый runtime с
Compose), затем выполните scripts/setup.sh. Команда создаёт .venv312, устанавливает
requirements.lock, подключает проект editable, готовит .env и поднимает сервисы.
Она не запускает marketplace scan и не импортирует реальные строки таблицы.

Существующий .env не перезаписывается. Новый создаётся с правами 0600 и случайным
DB_PASSWORD. Пароль не печатается. Для существующей БД нельзя менять DB_PASSWORD
простой заменой .env: пароль тома уже установлен и требует отдельной процедуры.

Стандартные порты: Web 127.0.0.1:18000, PostgreSQL 127.0.0.1:15433, Chrome CDP
127.0.0.1:19222. Хост-worker задаёт DATABASE_DSN на loopback, контейнер использует db:5432.
AUTH_ENABLED=false допустим только при этих локальных bind-адресах.
SCHEDULER_ENABLED=false предотвращает фоновые обращения контейнера к площадкам и
не заменяет интерактивный host Chrome runner.

## Каждый день

«Открыть дашборд.command» поднимает приложение и открывает отчёт.
«Запустить мониторинг.command» выполняет осторожный общий запуск с импортом
источника, сверкой каталогов продавца и журналом; caffeinate удерживает компьютер
от idle sleep на время процесса.
Эти файлы лежат в корне проекта на рабочем столе. Требуется активная сессия macOS.

Источник: SOURCE_GOOGLE_SHEET_EXPORT_URL, текущий gid=755848469. «Monitoring» в
интерфейсе обозначает локальный реестр; программа не запускает Apps Script для
обновления промежуточной вкладки Google Sheets. Если upstream-вкладка сама
устарела, чтение CSV не делает её свежей.

## Диагностика без обращения к площадкам

make check — тесты и lint. make doctor / scripts/smoke_stage.sh — окружение и HTTP.
Диагностика не проверяет фактический IP-маршрут VPN и точность парсинга площадок.
Проверьте отображаемые Москва/0 км, список новых автомобилей и страницы вручную
на контрольном реальном прогоне.

## Обновление

Прочитайте CHANGELOG; при изменении схемы выполните make backup, затем make start.
Alembic применится автоматически. Данные находятся в Docker volume, не в образе.
Не используйте docker compose down -v для обычного обновления. Восстановление
проверяется scripts/restore_test.sh в отдельной БД, не поверх рабочей базы.
