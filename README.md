# A1 Monitoring

Контроль размещения автомобилей A1 Auto на Auto.ru, Avito и сайте компании.
Программа связывает реестр маркетинга с объявлениями, проверяет выдачу по фильтрам,
открывает прямые карточки и сохраняет историю с доказательствами для отдела продаж.

Версия приложения: **0.14.0**. Аудит и перенос: **14 сентября 2026**.
Основная папка исходников: `/Users/filaret/Desktop/Monitoring`.
Целевая эксплуатация: MSI, Windows 11, Intel Core Ultra 5 125U, 16 ГБ RAM.

## С чего начать

| Задача | Документ |
| --- | --- |
| Ежедневная работа менеджера и оператора | [Рабочая инструкция](docs/operations/OPERATOR.md) |
| Установка Windows / сохранённый Mac | [Окружение и запуск](docs/operations/ENVIRONMENT.md) |
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
| Все документы и архив | [Оглавление](docs/README.md) |

## Текущее состояние

Реализованы импорт с защитой от части изменений схемы, видимый Chrome, сверка кабинетов
продавца, поисковый мониторинг, проверка прямых ссылок, история и локальный дашборд.
AI анализирует сохранённые данные и изображения последовательно. Подключение
платных LLM API пока только рассчитано — интеграция не выполнена.

Windows-перенос подготовлен кодом, но живой цикл на MSI не принят. Поисковый,
сверочный и карточечный контуры теперь связаны `cycle_id` и неизменяемым roster
manifest. Идентичность `Vehicle`/`Offer` отделена от старой проекции `Listing`;
машины без VIN не склеиваются по похожим полям, а перевыкладка требует явного
подтверждения оператора. Это ещё не заменяет живую приёмку.
Поэтому зелёные unit-тесты не означают подтверждённую точность мониторинга.

## Быстрый запуск

На этом Mac, только проверка площадок без загрузки локальной LLM:

```bash
cd /Users/filaret/Desktop/Monitoring
./scripts/start_local.sh
./scripts/local_scan.sh --engines auto_ru,avito --pages 3 --pace cautious
```

Видимый Chrome использует отдельный постоянный профиль. Перед живым прогоном
должен работать прямой доступ к площадкам. CAPTCHA требует действия оператора.

Windows после переноса файлов и установки окружения:

```powershell
Set-Location C:\work\A1_Monitoring
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\start_windows.ps1 -OpenDashboard
.\scripts\local_scan_windows.ps1 -Engines auto_ru,avito -Pages 3 -Pace cautious
```

Полный сценарий с локальными Hermes/Ouroboros:
`scripts/run_full_monitoring_windows.ps1`; на Mac — `scripts/run_full_monitoring_macos.sh`.
Они требуют дополнительной AI-установки и имеют ограничения из аудита.

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

Перенос выполнен перемещением, без второй копии 10 ГБ. Старый путь
`/Users/filaret/Desktop/a1_search_monitor_noapi_product` — совместимая символическая
ссылка на `Monitoring`. Она пока нужна старым shebang, AI-настройкам и JSON-путям.
См. [акт переноса](docs/operations/MIGRATION_2026-09-14.md).

## Проверка кода

```bash
.venv312/bin/python -m pytest -q
.venv312/bin/python -m ruff check src tests scripts tools
git diff --check
```

Эти команды не посещают площадки. Подробная приёмка описана в рабочей инструкции.
Правила продукта: [BIBLE.md](BIBLE.md). Учёт изменений: [CHANGELOG.md](CHANGELOG.md).
