# Окружение и установка

Проверено по файлам проекта 14.09.2026. Целевой production-контур — Windows 11
на MSI / 16 ГБ; его живой runtime ещё **не принят**. Mac/stage подтверждён только
в границах [stage gate](../production/STAGE_GATE_2026-09-14.md). Каноническая
рабочая папка на Mac: `/Users/filaret/Desktop/Monitoring`.

## Компоненты

| Компонент | Для чего | Где исполняется |
| --- | --- | --- |
| Python 3.12 + `.venv312` | Управление обходом и AI-пакетами | На основном компьютере |
| Chrome + Playwright/CDP | Видимый обход площадок | На основном компьютере |
| FastAPI / Jinja2 | Локальный дашборд | Контейнер app |
| PostgreSQL 16 / SQLAlchemy / Alembic | Реестр, история, миграции | Контейнер db |
| Docker Compose / backup | Запуск и резервирование | Docker Desktop |
| Hermes / Ouroboros | Последовательный анализ доказательств | Отдельные локальные окружения |
| llama.cpp / GGUF / vision-проектор | Локальный текстовый и визуальный inference | Хост, только при AI-режиме |
| pytest / Ruff | Проверка кода | Окружение разработчика |

Пакеты приложения закреплены в `requirements.lock`; AI — в
`config/ai-tools.lock` и профилях. `pyproject.toml` допускает Python >=3.11, но
скрипты установки рассчитаны на 3.12: для воспроизводимости использовать 3.12.
Не устанавливать все зависимости повторно при каждом запуске.

## Установка Windows с чистого компьютера

1. Установить Git for Windows, Python 3.12 с launcher `py`, Google Chrome,
   Docker Desktop с WSL2 и PowerShell 7 (`pwsh.exe` нужен AI-установщику).
   Дождаться работающего Docker Engine.
2. Подготовить `C:\work\A1_Monitoring` и клонировать согласованный commit из
   [A1_Monitoring](https://github.com/SilverCatSC/A1_Monitoring). Проверить, что
   `git status --short` пуст. Не использовать прежний macOS путь
   `a1_search_monitor_noapi_product`: это compatibility symlink, а не рабочий
   каталог новой установки.
3. Не переносить Mac `.venv*`, исполняемые AI-окружения, cookies Chrome и
   платформенные binaries. Они создаются заново. Модели GGUF можно перенести с
   проверкой контрольных сумм; снимки/JSON нужны для истории отдельно от БД.
4. Передать `.env` защищённо, не через публичный Git. Не печатать его в отчётах.
5. В PowerShell:

```powershell
Set-Location C:\work\A1_Monitoring
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup_windows.ps1
.\scripts\start_windows.ps1 -OpenDashboard
```

`setup_windows.ps1` устанавливает зависимости из lock-файла, приложение editable,
готовит `.env`, если его нет, и запускает контейнеры. Существующая `.env` сохраняется.
История с Mac автоматически не переносится: нужен backup/restore и снимки.

Не добавлять `-SkipDocker`, если требуется работающее приложение: этот режим
проверяет только Python-часть.

6. Сначала принять обход без AI по [рабочей инструкции](OPERATOR.md).
7. Только если нужен локальный AI:

```powershell
.\scripts\install_ai_tools_windows.ps1
.\scripts\download_local_model_windows.ps1
.\scripts\run_full_monitoring_windows.ps1
```

Дополнительные параметры и исторические установочные команды:
[Windows 11](../WINDOWS_11_INSTALL.md). Ограничения загрузки моделей и запуска
PowerShell на путях с пробелами: [аудит](../analysis/AUDIT_2026-09-14.md).
Наличие установщика не означает проведённой приёмки на Windows.

## Сеть перед живым обходом

VPSUS остаётся включённым: не выключайте VPN и не закрывайте ChatGPT/Codex ради
запуска Monitoring. До controlled cycle нужно подтвердить split-tunnel в обычном
Chrome: ChatGPT доступен по согласованному VPN/direct-маршруту, а `auto.ru` и
`avito.ru` — по утверждённым прямым browser rules. Сохраните только read-only
свидетельство настроек и по одной обычной странице каждого домена. Нельзя менять
VPN, reconnect или bypass-правила без отдельного подтверждения владельца.

Результат текущей Mac-проверки и незакрытые границы описаны в
[VPSUS split-tunnel gate](../production/VPN_GATE_2026-09-14.md); на MSI этот
контроль повторяется и остаётся частью M7 acceptance.

## Существующий Mac

Окружение перенесено вместе с проектом. Повторная загрузка модели не нужна.
Старый путь оставлен символической ссылкой для совместимости.

```bash
cd /Users/filaret/Desktop/Monitoring
.venv312/bin/python --version
.venv312/bin/python -c 'import app; print(app.__file__)'
./scripts/start_local.sh
```

Если нужно создать окружение на другом Mac, см.
[установку macOS](../MACOS_INSTALL.md) и [установочные скрипты](../INSTALL_SCRIPTS_MACOS.md).
Сначала основной мониторинг, затем отдельно AI. Установка не равна запуску
площадок; не смешивать её с проверкой реальной выдачи.

## Локальные адреса и настройки

| Параметр | Значение существующего окружения / назначение |
| --- | --- |
| `APP_BIND_PORT` | 18000, локальный HTTP |
| `DB_BIND_PORT` | 15433, PostgreSQL для Python на хосте |
| CDP Chrome | 19222, только localhost |
| LLM | 18080, только localhost |
| `COMPOSE_PROJECT_NAME` | `a1_search_monitor_noapi`, сохранить для прежних volumes |
| `SCHEDULER_ENABLED` | false: container scheduler не является host-Chrome scheduler |
| NETWORK_PROFILE | Метка происхождения запуска; не настройка VPN |

Не открывать CDP, БД и LLM-порт в локальную сеть/интернет. В текущем локальном
окружении аутентификация приложения отключена; публикация такого сервиса наружу
требует отдельной настройки доступа.

## Повторный запуск на Windows

`local_scan_windows.ps1 -Watch` — только ручной foreground-loop открытого
PowerShell: он не переживает logoff/сон и не должен работать одновременно с другим
worker. Для production-расписания не включать `SCHEDULER_ENABLED=true`: web-container
не управляет видимым Chrome пользовательской сессии.

После ручного MSI controlled cycle и owner review применяется отдельный
`run_monitoring_host_windows.ps1`: один cautious scan, интерактивный desktop,
межсессионный mutex и privacy-safe status. Планировщик должен регистрироваться
скриптом `register_monitoring_task_windows.ps1` сначала без `-Apply`, затем отдельно
с `-Apply`; задача запускается только как Interactive user и не вложенно с
`--watch`. Это future gate, а не принятое расписание. Полный порядок — в
[Windows handoff](../WINDOWS_11_INSTALL.md#host-runner-и-windows-task-scheduler--только-после-gate-4).

## Память

Малые задания снижают размер контекста, но не размер загруженных весов модели.
На Windows предусмотрены фазы browser → AI; проверка свободных 7500 МБ — лишь
порог запуска, не гарантия достаточной памяти во время vision. Остановка
контейнеров не гарантирует немедленного возврата памяти WSL2.

После перехода на LLM API локальные веса и vision-проектор не нужны для анализа;
Chrome, Python и БД всё равно занимают память. Размер экономии RAM необходимо
измерить на конкретном ПК. До API-интеграции не подменять URL модели в профилях:
нужны управление ключами, поддержка image-формата, учёт usage и лимиты бюджета.

## Диагностика

```bash
# На Mac; не обращается к площадкам.
.venv312/bin/python scripts/doctor.py --http
.venv312/bin/python -m pytest -q
.venv312/bin/python -m ruff check src tests scripts tools
```

На Windows использовать `.venv312\Scripts\python.exe` вместо Unix-пути.
Ошибка подключения БД/HTTP — проблема окружения; `429` на площадке — другая
причина, переустановка зависимостей её не устраняет.
