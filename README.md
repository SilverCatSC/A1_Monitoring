# A1 Monitoring

Контроль размещения автомобилей A1 Auto на Auto.ru, Avito и сайте компании.
Программа связывает реестр маркетинга с объявлениями, проверяет выдачу по фильтрам,
открывает прямые карточки и сохраняет историю с доказательствами для отдела продаж.

Версия приложения: **0.14.0**. Аудит и перенос: **14 сентября 2026**.
Основная папка исходников: `/Users/filaret/Desktop/Monitoring`.
Основной эксплуатационный host: текущий MacBook с macOS и видимым Chrome в
интерактивной пользовательской сессии. Windows/MSI сохранён только как
резервный, пока не принятый handoff.

## С чего начать

| Задача | Документ |
| --- | --- |
| Ежедневная работа менеджера и оператора | [Рабочая инструкция](docs/operations/OPERATOR.md) |
| Основной MacBook / резервный Windows | [Окружение и запуск](docs/operations/ENVIRONMENT.md) |
| Устройство и этапы проверки, карта алгоритма | [Алгоритм](docs/architecture/ALGORITHM.md) |
| Файлы, данные, точки входа | [Структура проекта](docs/architecture/PROJECT_STRUCTURE.md) |
| Реальные ограничения и обнаруженные дефекты | [Аудит 14.09.2026](docs/analysis/AUDIT_2026-09-14.md) |
| Как улучшать, стоит ли переписывать | [Варианты развития](docs/analysis/OPTIMIZATION.md) |
| Стоимость перехода с локальной LLM на API | [Расчёт затрат](docs/finance/API_COSTS.md) |
| Единый справочник проекта и листинг скрипта | [Project dossier](docs/PROJECT_DOSSIER.md) |
| Поддержка, тесты, резервирование, изменения | [Руководство разработчика](docs/operations/MAINTENANCE.md) |
| Идентичность без VIN и ручная перевыкладка | [M2 Stable identity](docs/production/STABLE_IDENTITY.md) |
| Evidence, blocked/CAPTCHA и безопасная смена вёрстки | [M3 Evidence contract](docs/production/EVIDENCE_CONTRACT.md) |
| Цена, НДС, статус, missing/ghost Offer и действия | [M4 Offer reconciliation](docs/production/OFFER_RECONCILIATION.md) |
| Роли, обратная связь и отчёт по исключениям | [M5 Operator release](docs/production/OPERATOR_RELEASE.md) |
| Controlled retry, backup и public allowlist | [M6 Operations](docs/production/OPERATIONS.md) |
| Живая приёмка, VPN и rollback | [M7 Acceptance](docs/production/ACCEPTANCE_M7.md) |
| Повседневный VPN-допуск MacBook и его ограничения | [VPN operational policy](docs/production/VPN_OPERATIONAL_POLICY_2026-09-25.md) |
| ID в фидах и новых карточках | [Сверка в цикле](docs/production/PLACEMENT_CYCLE_INTEGRATION_2026-09-24.md) |
| Откуда берётся unique_id во вкладке «Автомобили» | [Контракт отображения ID](docs/production/TASK_LISTINGS_UNIQUE_ID_2026-09-25.md) |
| Упрощение списка автомобилей и оставшиеся UX-задачи | [Задача по интерфейсу](docs/production/TASK_VEHICLE_LIST_UX_2026-09-25.md) |
| Решение о primary host и будущий LaunchAgent | [MacBook primary-host decision](docs/production/MACBOOK_PRIMARY_HOST_2026-09-14.md) |
| Все документы и архив | [Оглавление](docs/README.md) |

## Текущее состояние

Реализованы импорт с защитой от части изменений схемы, видимый Chrome, сверка кабинетов
продавца, поисковый мониторинг, проверка прямых ссылок, история и локальный дашборд.
AI анализирует сохранённые данные и изображения последовательно. Подключение
платных LLM API пока только рассчитано — интеграция не выполнена.

MacBook назначен основным host, но назначение не заменяет живую приёмку: VPSUS
split-tunnel, контрольная выборка, роли и ручной Mac shadow-run по-прежнему
являются M7-gates. Windows-перенос подготовлен кодом, но остаётся резервным и
непринятым. Поисковый, сверочный и карточечный контуры теперь связаны `cycle_id`
и неизменяемым roster manifest. Идентичность `Vehicle`/`Offer` отделена от старой
проекции `Listing`; машины без VIN не склеиваются по похожим полям, а
перевыкладка требует явного подтверждения оператора. Это ещё не заменяет живую
приёмку.
Поэтому зелёные unit-тесты не означают подтверждённую точность мониторинга.

## Быстрый запуск

На этом Mac безопасный старт — readiness-only preflight без обращения к
площадкам и без загрузки локальной LLM:

```bash
cd /Users/filaret/Desktop/Monitoring
./scripts/run_monitoring_host_macos.sh --preflight
```

Finder-ярлык «Запустить мониторинг.command» делает тот же preflight и затем
открывает локальный dashboard; двойной клик никогда не создаёт monitoring cycle.

Обычный host-runner требует приватную локальную политику маршрутов и текущий
статус `Connected` для VPSUS; это не закрывает Gate 1/M7 и не разрешает обход
ограничений площадок. Контролируемый цикл через Mac-host путь запускают только
при допустимом штатном доступе к обеим площадкам:

```bash
./scripts/run_monitoring_host_macos.sh --engines auto_ru,avito --pages 3
```

Видимый Chrome использует отдельный постоянный профиль. Перед разрешённым живым
прогоном VPSUS остаётся включённым: ChatGPT/Codex продолжает работать по
согласованному VPN/direct-маршруту, а Auto.ru и Avito — по утверждённой owner
policy. Не выключайте VPN и не закрывайте ChatGPT как workaround. Текущая
конфигурация ещё не является полным доказательством split-tunnel; CAPTCHA
требует действия оператора. Граница проверки —
[VPSUS split-tunnel gate](docs/production/VPN_GATE_2026-09-14.md).

Автоматического production-расписания пока нет. Единственный путь полного
marketplace-cycle и retry — `run_monitoring_host_macos.sh` на MacBook; retry
оператор запускает только как `make retry-cycle CYCLE_ID=<UUID>`, который
делегирует этому runner. Низкоуровневые `local_scan.sh`, `local_scan.py --watch`
и `make watch` теперь намеренно отказываются; это не запасные точки входа.
`scripts/run_full_monitoring_macos.sh` и `scripts/probe_page.py` также
fail-closed. Последний не заменяется «ручным probe»: `--probe-url` на macOS —
только non-DB диагностика, не M7/prod cycle.

`SCHEDULER_ENABLED=true` запрещён во всех окружениях. Будущий per-user
LaunchAgent остаётся отдельным MacBook gate: он не может заменить видимый
Chrome/VPSUS, не запускается при lock screen и не является принятым расписанием.
Детали — [primary-host decision](docs/production/MACBOOK_PRIMARY_HOST_2026-09-14.md).

Windows/MSI — deliberately fail-closed fallback. Его scanner/full/watch/host
runner, а также регистрация Windows Task `-Apply`, явно отказываются и не могут
создать marketplace cycle. На Windows допустимы лишь установка, локальная
readiness-диагностика и dashboard до нового отдельного решения владельца; см.
[Windows handoff](docs/WINDOWS_11_INSTALL.md).

Дашборд: [127.0.0.1:18000](http://127.0.0.1:18000/api/v1/dashboard).
Карточки отдела продаж: [Автомобили](http://127.0.0.1:18000/api/v1/dashboard/listings).

## Стек и хранение

Python 3.12; Playwright + Google Chrome; BeautifulSoup; FastAPI + Jinja2;
PostgreSQL 16 + SQLAlchemy/Alembic; Docker Compose; pytest/Ruff.
AI: Hermes, Ouroboros, llama.cpp, Qwen3.5-9B Q4_K_M с vision-проектором.
Версии Python-пакетов закреплены в `requirements.lock`, AI-настройки — в `config`.

Исходники находятся в `src/app`, запускатели — в `scripts`, вспомогательные
инструменты — в `tools`. Локальные модели, снимки и JSON-отчёты — в `artifacts`.
История PostgreSQL и автоматические backups находятся в Docker volumes,
**не внутри папки проекта**. `.env`, runtime и рабочие данные исключены из Git.

Канонический путь — `/Users/filaret/Desktop/Monitoring`. Прежний путь
`/Users/filaret/Desktop/a1_search_monitor_noapi_product` остаётся только
compatibility symlink для исторических локальных артефактов и не должен появляться
в новых командах или Windows-инструкциях. См. [акт переноса](docs/operations/MIGRATION_2026-09-14.md).

## Проверка кода

```bash
.venv312/bin/python -m pytest -q
.venv312/bin/python -m ruff check src tests scripts tools
git diff --check
```

Эти команды не посещают площадки. Подробная приёмка описана в рабочей инструкции.
Правила продукта: [BIBLE.md](BIBLE.md). Учёт изменений: [CHANGELOG.md](CHANGELOG.md).
