# A1 Search Monitor (No-API monitoring service)

Система для автоматического мониторинга видимости объявлений A1 в выдаче Auto.ru и Avito без API площадок, без локального ПК и с сохранением истории по каждому объявлению и фильтру.

## Что это даёт

- подтверждённый контроль страниц **1–3** по каждому утверждённому фильтру;
- разделение: `факт показа` vs `технический пробел`;
- автоматическое формирование эпизодов отсутствия (с подтверждением минимум 2 подряд);
- устойчивость к изменению структуры исходной таблицы (перестановка колонок, лишние колонки, новые алиасы);
- отчётность по выходным и ручная обратная связь менеджеров.

## Что входит в продукт

- `src/app` — API, импортер, планировщик, мониторинг, отчёты, модели;
- `tests` — минимальный автоматический контроль регрессии;
- `scripts` — развёртывание и smoke-проверки;
- `docs/` — технический контракт и критерии приемки;
- `Dockerfile` + `docker-compose.yml` — запуск в cloud-first режиме;
- `README.md`, `INSTRUCTION.md`, `BIBLE.md` — продуктовый контур и правила эксплуатации.

## Что **не** используется

- API Auto.ru и Avito;
- локальный компьютер как обязательный элемент запуска;
- ручной разбор HTML в живом браузере при каждом запуске.

Используются только публичные страницы результатов поиска через Playwright.

## Архитектурная идея (кратко)

```text
Источник (CSV/XLSX экспорт подрядчика)
    -> Импортер ссылок и фильтров (с проверкой сигнатур полей)
    -> PostgreSQL (реестр + история наблюдений)
    -> Scheduler (плановый запуск)
    -> Адаптеры Auto.ru/Avito (Playwright + парсинг первых 3 страниц)
    -> Классификатор наблюдений
    -> KPI-отчёты + API дашборд
    -> Feedback от менеджеров (канал качества)
```

## Рабочая папка

Проект уже создан в вашей рабочей директории:

- `/Users/filaret/Desktop/a1_search_monitor_noapi_product`

Если хотите, могу создать ещё одну копию с другим именем (`A1_Search_Monitor`) и сделать её стартовой для всех ваших запусков.

## Быстрый запуск stage-окружения

```bash
cd /Users/filaret/Desktop/a1_search_monitor_noapi_product
cp .env.example .env
./scripts/deploy.sh
```

Проверить:

```bash
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/ready
curl http://localhost:8000/api/v1/status/imports
```

## Локальный запуск (для проверки)

```bash
python3.12 -m venv .venv312
source .venv312/bin/activate
python -m pip install -e ".[dev]"
python -m playwright install chromium

python -m app.cli init
python -m app.cli import-source
python -m app.cli run-cycle
python -m app.cli serve
```

## Что важно в поведении (инварианты)

- `ABSENCE` считается только после полноценного сканирования всех трёх страниц;
- технический сбой (`UNKNOWN_TECHNICAL`, `TECHNICAL_ERROR`) не трактуется как факт отсутствия;
- при проблемной структуре таблицы активный реестр не переписывается без ручного подтверждения;
- выходные учитываются как штатный рабочий ритм 7/7 с отдельной аналитикой по «суббота/воскресенье».

## Текущий статус

Папка содержит рабочий stage-продукт. Метка «production» допускается только после выполнения
критериев из `docs/ACCEPTANCE_CRITERIA.md`, облачного HTTPS-deploy и контрольного пилота.
Локальный Docker Compose является проверкой релизного образа, но не доказательством автономной
работы при выключенном компьютере.

## Основные API

- `GET /api/v1/health`  
- `GET /api/v1/ready`  
- `POST /api/v1/import`  
- `POST /api/v1/scan`  
- `GET /api/v1/dashboard/kpi`  
- `GET /api/v1/dashboard/missing`  
- `POST /api/v1/feedback`  
- `GET /api/v1/status/imports`

## Связанные исходники и ссылки

- [Исходная таблица](https://docs.google.com/spreadsheets/d/1afmXPsek-nu-1eH_n1PuYNIIIYcskJqwcSCIMHzJUNA/edit?gid=1301771142#gid=1301771142)
- [Auto.ru dealer](https://auto.ru/diler/cars/all/a1_avto_moskva/)
- [Auto.ru dealer LCV](https://auto.ru/diler/lcv/all/a1_avto_moskva/)
- [Avito профиль](https://www.avito.ru/brands/a1auto/items/all/avtomobili?s=profile_search_show_all&sellerId=e4036231f69cc5cfc365db2f79600230)

## Принцип приоритетов

1. Корректность фактов важнее скорости.
2. Автономность и предсказуемость важнее «красивого» интерфейса.
3. Прозрачность технических ошибок важнее их скрытия в сводке.
