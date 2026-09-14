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

## Interrupted-cycle recovery

The first real recovery record is
[INTERRUPTED_CYCLE_RECOVERY_2026-09-14.md](INTERRUPTED_CYCLE_RECOVERY_2026-09-14.md).

`KeyboardInterrupt` and a normalized `SIGTERM` now persist a terminal `failed`
ledger record before the runner exits. If an older runner ended before that
write, an admin/operator can perform a **non-scanning** recovery:

```bash
.venv312/bin/python -m app.cli recover-open-cycles
```

or call protected `POST /api/v1/cycles/recover-open`. The action first holds the
same complete-cycle lock used by scans. Only then it changes abandoned
`preparing`/`running` rows to `failed`, records `interrupted_runner_recovery`,
timestamp and actor, and leaves their manifest, imports, observations and other
terminal cycles untouched. The returned failed cycle becomes eligible for an
explicit ordinary retry. If a cycle is live, recovery returns conflict rather
than guessing that it is stale.

## Расписание и host Chrome

`SCHEDULER_ENABLED=true` внутри web-контейнера **не является решением для
видимого host Chrome**. Он может коалесцировать интервалы и предотвращать второй
container job, но не получает интерактивный Windows desktop, состояние VPSUS или
право управлять Chrome-профилем пользователя. Поэтому для реальных marketplace
циклов `SCHEDULER_ENABLED` остаётся `false`; не включайте его как обход отсутствия
host-runner.

`local_scan_windows.ps1 -Watch` также не является production scheduler: это
ручной foreground-loop в открытом PowerShell. Он заканчивается при logoff/сне и
не должен работать параллельно с другой ручной командой либо Task Scheduler.

Production-контракт Windows использует отдельный
`run_monitoring_host_windows.ps1`: ровно один cautious scan в интерактивном
desktop (не Session 0), межсессионный mutex, preflight HTTP, безопасное recovery
только abandoned cycles и privacy-safe
`artifacts/monitoring_host_runner_status.json`. При занятом mutex это штатный
`HOST_RUNNER_SKIPPED_ACTIVE`, а не повод запускать второй worker или retry.

`register_monitoring_task_windows.ps1` без `-Apply` только показывает план;
с `-Apply` отдельно создаёт ежедневную `\A1Monitoring\InteractiveCycle` для
текущего пользователя с
`LogonType=Interactive`, `RunLevel=Limited`, `MultipleInstances=IgnoreNew` и
`RestartCount=0`. Регистрация не запускает scan; trigger запускает один
host-runner, не `--watch` и не автоматический retry.

Включение этой задачи допустимо только после:

1. подтверждённого split-tunnel для ChatGPT, Auto.ru и Avito в обычном Chrome;
2. успешного ручного Windows controlled cycle с видимым Chrome;
3. проверки, что `--watch`, container scheduler и иные workers остановлены;
4. owner review плана задачи и статуса после первого trigger.

До этих фактов host-runner/Task Scheduler остаются M7 gate, а не принятым
production-расписанием. Детальная Windows-процедура:
[WINDOWS_11_INSTALL.md](../WINDOWS_11_INSTALL.md#host-runner-и-windows-task-scheduler--только-после-gate-4).

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
