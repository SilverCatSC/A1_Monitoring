# M6: управляемые операции

Статус: M6.1 реализован в коде 14.09.2026; M6 целиком ещё не принят. Документ
разделяет фактически реализованный controlled retry от будущей проверки
backup/restore и живого production-расписания.

## Controlled retry

`partial` или `failed` цикл можно повторить через:

```bash
.venv312/bin/python -m app.cli retry-cycle <cycle_id>
```

или защищённый маршрут `POST /api/v1/cycles/<cycle_id>/retry` роли `admin` либо
`operator`. Это **не** resume старого цикла:

1. Система проверяет, что исходный цикл уже завершён как `partial`/`failed`.
2. Запускается новый `MonitoringCycle` с новым ID и `retry_of_cycle_id`.
3. Повторно читается источник, создаётся новый privacy-bounded roster manifest и
   повторяются наблюдения через видимый локальный Chrome.
4. Старый manifest и его факты не изменяются.

Нельзя retry completed/running/preparing cycle. Запуск может обратиться к
Auto.ru/Avito, поэтому это явное действие оператора, а не кнопка «исправить
данные». CAPTCHA и технические ошибки остаются техническими результатами нового
цикла.

`GET /api/v1/status/operations` и поле `cycles` в `/api/v1/system/status`
показывают активные циклы, количество retryable попыток, последние 24-часовые
сбои и ID очереди. Они не заменяют анализ evidence.

## Расписание

При `SCHEDULER_ENABLED=true` scheduler запускает обычный полный цикл с
`SCAN_INTERVAL_MINUTES`. Job coalesces пропущенные интервалы, допускает только
один экземпляр и журналирует ошибку scheduler. Это предотвращает параллельные
процессы, но не является подтверждением, что Chrome, VPN-маршрут или площадка
доступны.

В production включать scheduler только после:

1. Проверки `NETWORK_PROFILE=local_browser`, видимого Chrome и источника.
2. Проверки, что приложение поднято в единственном экземпляре.
3. Контролируемого shadow-run и review статусов `/status/operations`.

## Publication allowlist

`scripts/export_public_report.py` не экспортирует dashboard, listing, историю,
feedback, activity, evidence или raw API. Он берёт только
`/api/v1/public/report`: отдельную страницу с агрегатами, статусами двух площадок
и счётчиками. Её projection не содержит VIN, URL, названия/цены автомобилей,
ID, текст/автора feedback или screenshot.

Каждый export требует локальный, ignored allowlist вне Git. Пример структуры:

```json
{
  "scope": "a1-aggregate-public-report-v1",
  "approved_by": "owner",
  "approved_at": "2026-09-14T10:00:00+03:00",
  "expires_at": "2026-09-21T10:00:00+03:00"
}
```

Он действует максимум 31 день, должен быть подписан по внутренней процедуре
владельцем и не может содержать разрешение на полный отчёт. Команда только
генерирует ignored `public/`; она не делает commit, push или Pages deploy:

```bash
make public-report ALLOWLIST=/secure/path/publication_allowlist.json
```

Если app защищено Basic-auth, передать **имя** переменной окружения с локальным
`username:password`, а не секрет в командной строке:

```bash
A1_EXPORT_AUTH='user:password' \
.venv312/bin/python scripts/export_public_report.py \
  --allowlist /secure/path/publication_allowlist.json --auth-env A1_EXPORT_AUTH
```

Скрипт останавливается, если output содержит неизвестные старые файлы, или HTML
содержит внутренние API/evidence references. После export нужны независимая
ручная проверка итогового `public/index.html`, owner approval и только затем
отдельный явный `publish_pages.ps1 -Push`/commit. Ни export, ни publish не были
выполнены в этой разработке.

## Открытые части M6

- Historical pre-deploy stop is superseded: the current Mac/stage backup and
  isolated restore-test completed at Alembic `20260914_0011`. Evidence:
  [STAGE_GATE_2026-09-14.md](STAGE_GATE_2026-09-14.md). Windows/MSI execution
  remains unverified.
- backup/restore сценарии для macOS и Windows проверяют checksum, dump, revision
  Alembic и контрольные количества; macOS result is recorded above, while the
  same procedure must still be executed and recorded on Windows/MSI;
- нужно провести owner review фактического aggregate-only HTML перед любым
  публичным export/publish;
- мониторинг backup-age и controlled retry на Windows/MSI требуют отдельной
  живой приёмки.

Пока эти пункты не закрыты, M6 не повышает релиз до `0.15.0` и не заменяет M7.
