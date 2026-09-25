# Структура проекта и ответственность файлов

Проверено по исходникам 14.09.2026. Приложение — модульный Python-монолит с
запускателями двух ОС; это не набор самостоятельно развёрнутых микросервисов.

```text
Monitoring/
├── README.md                    точка входа
├── BIBLE.md                     требования и смысл состояний
├── INSTRUCTION.md               маршрутизация рабочих инструкций
├── CONCEPT.md                   цель и границы продукта
├── CHANGELOG.md                 история изменений
├── src/app/
│   ├── importer/                чтение и нормализация таблиц
│   ├── scraper/                 Chrome, парсеры, границы выдачи, прямые карточки
│   ├── service/                 цикл, сверка, наблюдения, аналитика, замечания
│   ├── templates/, static/      серверный HTML/CSS/JS
│   ├── models.py, schemas.py    хранение и API-контракты
│   ├── api.py, main.py, cli.py  HTTP, приложение, CLI
│   └── config.py, db.py         настройки и подключения
├── scripts/                     стабильные точки запуска ОС и AI-этапов
├── tools/                       офлайн-анализ и обслуживание проекта
├── config/
│   ├── ai-tools.lock            версии и параметры локального AI
│   ├── hermes-*.yaml            профили аналитика и инженера
│   ├── ouroboros-*.template     профили координатора
│   └── costs/                   датированные тарифы и допущения сметы
├── alembic/versions/             история изменений схемы БД
├── tests/unit/, integration/    изолированные проверки
├── docs/
│   ├── operations/              инструкции эксплуатации и поддержки
│   ├── architecture/           фактическое устройство и алгоритм
│   ├── analysis/               аудит и предлагаемые изменения
│   ├── finance/                затраты и выбор AI
│   └── archive/                сохранённые предыдущие редакции
├── deploy/cloud/                старый серверный вариант, не основной запуск
├── .github/workflows/           публикация Pages; ещё требует продуктовой приёмки
├── docker-compose.yml           app/db/backup и mounts
├── Dockerfile                   образ приложения
├── requirements.lock            воспроизводимый набор Python-зависимостей
├── pyproject.toml               метаданные пакета и настройки тестов
├── .env                         приватные настройки текущего компьютера
├── .venv312/                    локальное окружение; не переносится между ОС
└── artifacts/                   приватные рабочие данные; не Git
```

## Где искать функцию

| Ответственность | Файл / функция |
| --- | --- |
| Единственный live entrypoint / видимый Chrome | `scripts/run_monitoring_host_macos.sh`; guarded child `scripts/local_scan.py`, `_ensure_local_chrome`, `main` |
| Импорт → кабинеты → поиск → карточки | `src/app/service/cycle.py`, `MonitoringCycleService` |
| Защита импорта и обновление реестра | `src/app/importer/service.py`, `SourceImporter` |
| Назначение фильтров | `src/app/service/filters.py`, `FilterRegistryService` |
| Каталоги продавца | `src/app/service/dealer_discovery.py` |
| Проверка связи машины и URL | `src/app/service/reconciliation.py` |
| Позиции, наблюдения, эпизоды непоказов | `src/app/service/monitor.py` |
| Разбор Auto.ru / Avito | `src/app/scraper/auto_ru.py`, `avito.py` |
| Состояние прямой карточки и НДС | `src/app/scraper/seller.py` |
| Головная таблица / сайт A1Auto | `src/app/service/head_table_audit.py`, `company_site_audit.py` |
| Сборка AI-пакетов | `scripts/build_live_agent_packet.py`, `build_ai_work_units.py` |
| Малые задания Hermes | `scripts/run_ai_work_units.py` |
| Проверка структуры AI-вывода | `scripts/validate_ai_review.py`, `validate_staged_review.py` |
| Legacy full-cycle wrappers | `scripts/run_full_monitoring_macos.sh`, `run_full_monitoring_windows.ps1` — намеренно fail-closed |
| Controlled retry | `Makefile` target `make retry-cycle CYCLE_ID=<UUID>` → Mac host runner |
| Контекст HTML-отчётов | `src/app/service/report.py` |
| Офлайн-смета API | `tools/estimate_api_cost.py` |

## Что исправлено в структуре

9 предыдущих документов физически перемещены в `docs/archive`; относительные
Markdown-ссылки пересчитаны. Корневые README/BIBLE/INSTRUCTION/CONCEPT заменены
короткими рабочими редакциями. Новые документы разделены по назначению.
Пустые `infrastructure`, `monitoring`, `services`, `web` удалены: они создавали
ложное впечатление отдельных подсистем, но не содержали файлов.

`scripts` пока сохраняет прежние пути: shell-скрипты вычисляют корень через `..`,
профили Ouroboros и команды оператора ссылаются на эти адреса. Механическое
раскладывание по `macos/windows/ai` без изменения этого контракта сломало бы запуск.
Следующий обоснованный шаг — единый Python-координатор и тонкие оболочки ОС;
после его внедрения внутренние функции можно вынести из `scripts` в пакет.

## Данные и Git

| Данные | Место | Перенос / учёт |
| --- | --- | --- |
| Исходники, docs, tests, config-шаблоны | проект | Git |
| Секреты и пароли | `.env` | приватно; не Git |
| Поисковые и прямые снимки | `artifacts/evidence` | приватный backup |
| Аудиты таблицы и сайта | `artifacts/head_table_audits`, `company_site_audits` | приватный backup |
| Пакеты и ответы AI | `artifacts/agent_work_units`, `agent_reviews`, `ouroboros_reviews` | приватный backup |
| Chrome startup profile | `artifacts/local_chrome_isolated_profile` | локально, без расширений; страницы открываются во временных приватных контекстах |
| Веса моделей и AI-окружения | `artifacts/models`, `hermes_agent`, профили | отдельно; модель можно перенести, окружение переустановить |
| Автомобили, наблюдения, замечания | PostgreSQL volume `db_data` в Compose-проекте | `pg_dump`/`pg_restore` |
| Автоматические дампы | отдельный volume `db_backups` | вынести хотя бы одну копию вне Docker |

Имена volumes включают имя Compose-проекта. Здесь сохранено
`COMPOSE_PROJECT_NAME=a1_search_monitor_noapi`, поэтому переименование каталога
не создаёт автоматически новую рабочую БД. На чистом ПК история сама не появится.

`report.py` (1087 строк), `api.py` (680), `monitor.py` (652), `filters.py` (626)
и `head_table_audit.py` (572) — кандидаты на дальнейшее выделение небольших модулей.
Размер файла сам по себе не дефект; основание — смешение ответственности и сложность
изолированной проверки. Делить следует по продуктовым функциям, не по числу строк.
