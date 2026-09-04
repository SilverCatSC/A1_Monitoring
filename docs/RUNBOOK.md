# Руководство по запуску A1_Search_Monitor

Документ описывает порядок первичного запуска и повседневной эксплуатации.

## 1. Первичная инициализация локально

1. Откройте папку проекта.
2. Заполните `.env` на основе `.env.example`.
3. Запустите:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m playwright install chromium
```

4. Инициализация БД:

```bash
python -m app.cli init
```

5. Загрузка исходных данных из CSV/XLSX:

```bash
python -m app.cli import-source --path /path/to/source-table.csv
```

## 2. Запуск API и первого цикла

```bash
python -m app.cli serve
```

Для разового цикла:

```bash
python -m app.cli run-cycle
```

## 3. Docker-подъем

```bash
cp .env.example .env
./scripts/deploy.sh
```

Проверка:

```bash
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/dashboard/kpi
```

## 4. Что обязательно проверять после изменений в структуре таблицы

1. Проверить загрузку: `GET /api/v1/status/imports`
2. При `blocked_by_schema_drift=true` не продолжать автопубликацию результатов
3. Зафиксировать блокировку в отчете инженера
4. Запустить импорт только после уточнения/обновления матчинга заголовков

## 5. Отклик по качеству данных

Сотрудники создают обратную связь через `/api/v1/feedback` (UI может отправлять тот же payload).

Поддерживаемые статусы: `new`, `checking`, `assigned`, `fixed`, `confirmed`.

## 6. Выключение

```bash
docker-compose down
## 7. Примечание по окружению

Если `docker-compose`/`docker compose` есть, но daemon не доступен (например, отсутствует socket Colima/Docker Desktop), запуск остановится с диагностикой.
```
