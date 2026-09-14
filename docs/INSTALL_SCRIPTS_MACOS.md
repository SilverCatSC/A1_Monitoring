# Установочные скрипты A1 Monitoring для macOS

Актуально для основного MacBook host на 14.09.2026. Назначение MacBook основным
контуром не равно живой M7-приёмке: VPSUS, shadow-run и будущий LaunchAgent имеют
отдельные gates. Windows/MSI сохранён как резервный, непринятый handoff.

## Одна команда для полной установки

```bash
cd /Users/filaret/Desktop/Monitoring
./scripts/install_all_macos.sh
```

Устанавливаются и настраиваются:

1. Homebrew, если отсутствует.
2. Python 3.12, Git, Google Chrome и Docker Desktop либо используется уже
   работающий совместимый Docker-runtime.
3. Изолированное окружение проекта и зависимости из `requirements.lock`.
4. PostgreSQL, FastAPI-приложение и контейнер резервного копирования.
5. `uv`, `llama.cpp`, клиент `hf`.
6. Hermes, закреплённый на проверенном commit, в изолированном профиле проекта.
7. Q00/Ouroboros с MCP-профилем Hermes.
8. Локальная модель Qwen3.5-9B Q4_K_M и vision-проектор, около 6,2 ГБ.

Установщик не запускает обход Auto.ru/Avito. Он может запросить системный пароль
для Homebrew/Docker и требует интернет. Условия лицензирования Docker Desktop
нужно проверить для вашей организации.

Без немедленной загрузки модели:

```bash
./scripts/install_all_macos.sh --skip-model
```

Позже:

```bash
./scripts/download_local_model_macos.sh
./scripts/start_local_ai.sh
```

## Скрипты по отдельности

| Скрипт | Что делает |
| --- | --- |
| `install_macos_prerequisites.sh` | Устанавливает/проверяет системные инструменты |
| `setup.sh` | Создаёт `.venv312`, ставит Python-зависимости и поднимает приложение |
| `install_ai_tools_macos.sh` | Ставит llama.cpp, hf, Hermes и Ouroboros, создаёт local-only профиль |
| `download_local_model_macos.sh` | Загружает веса и vision-проектор, сохраняет оба SHA-256 |
| `start_local_ai.sh` | Поднимает llama-server только на `127.0.0.1:18080` |
| `stop_local_ai.sh` | Останавливает только процесс, чей PID и команда подтверждены |
| `run_ai_review_macos.sh` | Делит последний прогон по машинам и снимкам, запускает последовательный Hermes QA |
| `run_full_monitoring_macos.sh` | Обход двух площадок, затем Hermes QA и read-only аудит Ouroboros |
| `run_monitoring_host_macos.sh` | Static-only host runner; `--preflight` проверяет GUI host/Docker/readiness без цикла или Chrome |
| `register_monitoring_launchagent_macos.sh` | Plan-only per-user LaunchAgent registrar; требуется явный `--apply` |
| `hermes_maintenance_macos.sh` | Изолированный исполнитель Hermes для инженерного контура |
| `run_ouroboros_maintenance_macos.sh` | Запускает Ouroboros с проектным HOME и отключённой телеметрией |
| `check_full_system_macos.sh` | Проверяет приложение, инструменты, модель, checksum и локальный endpoint |

Версии и модель закреплены в `config/ai-tools.lock`. Профиль Hermes, модель,
логи и результаты находятся в игнорируемой Git папке `artifacts/`.

## Проверка после установки

```bash
./scripts/check_full_system_macos.sh
```

Ожидаемый итог: `FULL_MONITORING_SYSTEM_READY`. Проверка не посещает площадки.

## Ручной controlled cycle — только после соответствующего gate

Сохраните VPSUS включённым и подтвердите split-tunnel: ChatGPT/Codex продолжает
работать по согласованному маршруту, Auto.ru/Avito — по direct browser rules.
Не выключайте VPN и не закрывайте ChatGPT как workaround, затем:

```bash
./Запустить\ мониторинг.command
```

Команда, если оператор разрешил cycle, сначала проводит обход в видимом Chrome, а после него запускает
последовательные data/vision/synthesis-этапы Hermes. Малые этапы идут по одному,
с паузами и ограничением процессора. Затем Ouroboros проверяет компактный результат
как read-only аудитор. Артефакты сохраняются в `artifacts/agent_work_units/`,
`artifacts/agent_reviews/` и `artifacts/ouroboros_reviews/`. Для инженерных задач
Ouroboros использует отдельный HOME и отдельные git-worktree. Первый диагностический
вызов без изменения кода:

```bash
./scripts/run_ouroboros_maintenance_macos.sh doctor install
```

Изменяющий код цикл запускается только отдельной командой с явно сформулированной
задачей; результат проверяется тестами и человеком до переноса в рабочую ветку.

Подробности: [основная инструкция macOS](MACOS_INSTALL.md).

## Плановый запуск в активной macOS-сессии — отдельный gate

Container scheduler не управляет видимым Chrome, поэтому
`SCHEDULER_ENABLED` остаётся `false`. В целевой static-only схеме
`run_monitoring_host_macos.sh` выполняет один cautious cycle, а
`register_monitoring_launchagent_macos.sh` создаёт per-user GUI LaunchAgent
`com.silvercatsc.a1monitoring.interactive-cycle`.

План регистратора должен быть безопасным по умолчанию:

```bash
./scripts/register_monitoring_launchagent_macos.sh --at 09:00
```

Только после ручного review и отдельного разрешения используется `--apply`.
Регистрация сама не запускает marketplace cycle. LaunchAgent не должен быть
`LaunchDaemon`, не работает как обход lock screen/CAPTCHA, использует
`RunAtLoad=false` и `KeepAlive=false`, а `partial` не повторяет автоматически.
Это описание планируемого контракта, не заявление о выполненной регистрации или
живом тесте. Полный gate —
[MacBook primary-host decision](production/MACBOOK_PRIMARY_HOST_2026-09-14.md).
