# Документация проекта

Рабочая редакция: 14.09.2026. Для текущего состояния сначала читайте документы ниже.
Верхнеуровневые исторические технические документы сохранены как справочные;
при расхождении статус из аудита этой даты имеет приоритет над старыми обещаниями.

## Эксплуатация

- [PROJECT_DOSSIER](PROJECT_DOSSIER.md) — единый справочник проекта с приложением исходного скрипта.
- [OPERATOR](operations/OPERATOR.md) — запуск, чтение отчёта, замечания, приёмка.
- [ENVIRONMENT](operations/ENVIRONMENT.md) — Mac/Windows, окружение, команды.
- [MAINTENANCE](operations/MAINTENANCE.md) — изменения, диагностика и резервирование.
- [MIGRATION](operations/MIGRATION_2026-09-14.md) — что перенесено и что хранится отдельно.

## Архитектура и решения

- [ALGORITHM](architecture/ALGORITHM.md) — фактические этапы и Mermaid-карта.
- [PROJECT_STRUCTURE](architecture/PROJECT_STRUCTURE.md) — ответственность каталогов.
- [AUDIT](analysis/AUDIT_2026-09-14.md) — доказанные проблемы и неизвестное.
- [OPTIMIZATION](analysis/OPTIMIZATION.md) — план улучшений и варианты переписывания.
- [API_COSTS](finance/API_COSTS.md) — объёмы, формулы, тарифы, сценарии и окупаемость.

## Справочные материалы предыдущей реализации

- [Установка Windows](WINDOWS_11_INSTALL.md), [установка Mac](MACOS_INSTALL.md).
- [AI-агенты](AI_AGENTS.md), [стек](STACK.md), [данные](DATA_CONTRACT.md).
- [Сверка продавца](SELLER_RECONCILIATION.md), [метрики](METRICS.md), [UX](UX_UI.md).
- [Архитектура v0.9](ARCHITECTURE.md), [прежний статус релиза](RELEASE_STATUS.md).
- [Исторические отчёты](archive/README.md).

Документы с ценами содержат дату проверки и ссылки на официальные источники.
Воспроизводимый офлайн-расчёт находится в `tools/estimate_api_cost.py`.
