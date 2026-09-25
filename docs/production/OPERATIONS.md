# M6: управляемые операции

Статус: M6.1 реализован в коде 14.09.2026; M6 целиком ещё не принят. Документ
разделяет фактически реализованный controlled retry от будущей проверки
backup/restore и живого production-расписания.

## Controlled retry

`partial` или `failed` цикл можно повторить только через MacBook host runner:

```bash
make retry-cycle CYCLE_ID=<UUID>
```

Target делегирует retry тому же host runner, что и новый cycle; прямой
`python -m app.cli retry-cycle` и ручной API-вызов не являются operator entrypoint.
Это **не** resume старого цикла:

1. Система проверяет, что исходный цикл уже завершён как `partial`/`failed`.
2. Запускается новый `MonitoringCycle` с новым ID и `retry_of_cycle_id`.
3. Повторно читается источник, создаётся новый privacy-bounded roster manifest и
   повторяются наблюдения через видимый локальный Chrome.
4. Старый manifest и его факты не изменяются.

Нельзя retry completed/running/preparing cycle. Запуск может обратиться к
Auto.ru/Avito, поэтому это явное действие оператора, а не кнопка «исправить
данные». CAPTCHA и технические ошибки остаются техническими результатами нового
цикла.

Для новых циклов перечень известных непройденных этапов записан в
`artifacts/evidence/cycles/<cycle_id>/pending_checks.json`; количество и
разбивка доступны в `monitoring_cycles.summary.pending_checks`. Это отчёт
для разбора, **не команда продолжения**. `retry-cycle` по-прежнему повторяет
весь цикл, а не только записи из этого файла.

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

`SCHEDULER_ENABLED=true` отвергается во всех окружениях. Контейнер не получает
интерактивную macOS GUI-сессию, состояние VPSUS или право управлять
Chrome-профилем пользователя, поэтому допустимо только `SCHEDULER_ENABLED=false`.

`local_scan.sh`, `local_scan.py --watch` и `make watch` намеренно отказываются.
Они не являются scheduler, foreground fallback или способом обойти host runner.

Production-контракт основного MacBook использует отдельный, ещё не принятый
`run_monitoring_host_macos.sh`: ровно один cautious scan в активной GUI-сессии,
mutex, preflight HTTP, безопасное recovery только abandoned cycles и privacy-safe
status в `artifacts`. При занятом mutex это штатный skip, а не повод запускать
второй worker или retry.

Central cycle принимает только проверяемый inherited Mac host-lock FD, который
удерживает этот runner; `LOCAL_BROWSER_HOST_ADMISSION=true`, путь к lock-файлу или
VPN operational policy по отдельности не открывают цикл. Это operational anti-accidental
boundary, не hostile-security proof против того же локального пользователя.

`register_monitoring_launchagent_macos.sh --at HH:MM` показывает только plan-only
контракт. Если owner когда-либо одобрит применение, per-user GUI LaunchAgent
`com.silvercatsc.a1monitoring.interactive-cycle` обязан использовать ежедневный
`StartCalendarInterval`, `RunAtLoad=false` и `KeepAlive=false`. Регистрация не
запускает scan; trigger запускает один host-runner, не `--watch` и не
автоматический retry. Это LaunchAgent, не LaunchDaemon: запуск возможен только
после входа пользователя и не является способом обойти блокировку экрана или CAPTCHA.

Включение этой задачи допустимо только после:

1. подтверждённого split-tunnel для ChatGPT, Auto.ru и Avito в обычном Chrome;
2. успешного ручного MacBook controlled cycle с видимым Chrome;
3. проверки, что нет другого worker; `--watch` и container scheduler fail-closed;
4. явной проверки TCC/Desktop-доступа для GUI-пользователя, Chrome и launchd
   без автоматической выдачи permissions;
5. owner review плана задачи и статуса после первого trigger.

До этих фактов host-runner/LaunchAgent остаются static-only M7 gate, а не
принятым production-расписанием. При lock screen или отсутствии console GUI user
runner должен отказаться, а не начинать невидимый scan. Детали и границы —
[MACBOOK_PRIMARY_HOST_2026-09-14.md](MACBOOK_PRIMARY_HOST_2026-09-14.md).
Windows Task Scheduler, scanner/full/watch и host runner deliberately fail-closed
и не требуются для MacBook release.

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
  [STAGE_GATE_2026-09-14.md](STAGE_GATE_2026-09-14.md). This does not by itself
  accept a live MacBook cycle or scheduled runner.
- backup/restore сценарий macOS проверяет checksum, dump, revision Alembic и
  контрольные количества; его result is recorded above. Windows/MSI имеет
  отдельный unaccepted fallback procedure, а не блокирующий MacBook gate;
- нужно провести owner review фактического aggregate-only HTML перед любым
  публичным export/publish;
- мониторинг backup-age, controlled retry и, при необходимости, первый
  LaunchAgent trigger на MacBook требуют отдельной живой приёмки.

Пока эти пункты не закрыты, M6 не повышает релиз до `0.15.0` и не заменяет M7.
