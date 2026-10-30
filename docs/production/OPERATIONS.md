# M6: управляемые операции

Статус: M6.1 реализован в коде 14.09.2026; M6 целиком ещё не принят. Документ
разделяет фактически реализованный controlled retry от будущей проверки
backup/restore и живого production-расписания.

## Unattended service delivery target — 2026-10-28

The owner has prioritized unattended operation after a manual start on this
Mac while awake and signed in. On 2026-10-28 the owner explicitly chose to
pause indefinitely for CAPTCHA rather than finish early, and declined automatic
scheduled starts. Existing interactive execution is not service acceptance,
and enabling the retired container scheduler does not satisfy this target.

Delivery sequence:

1. Operational visibility: classify blocked CAPTCHA/access separately from
   fresh work and missing progress signals, including the card-reading phase.
   `/api/v1/status/scans/progress` now exposes `operational_health`. It assesses
   the recorded heartbeat only; it does not verify process liveness, report
   integrity or marketplace coverage. Fresh wait-loop events still mean blocked.
2. Durable browser checkpoints: implemented for completed search filters and
   fully read active cards, published atomically after each operation. A separate
   recovery attempt reuses matching intact checkpoints, preserving their original
   timestamps. Real process-exit and saved-data replay tests pass. An interrupted
   filter still restarts from its first page; per-page continuation is not yet
   implemented. The existing full retry remains distinct from recovery.
3. Assisted access, automatic continuation: keep unlimited CAPTCHA waiting and
   notify the local user when CAPTCHA or page access needs attention. The host
   runner now starts a read-only notification observer for full cycles and emits
   a terminal notification after the scan returns. Repeated wait heartbeats do
   not repeatedly notify. The observer releases its inherited host lock and is
   stopped with the runner. Notifications may be suppressed by macOS permissions
   or Focus; the dashboard remains the durable operational view. Delivery of a
   real notification and continuation after live CAPTCHA still need validation.
4. Manual host start: no schedule or automatic retry. Test sleep/login,
   interrupted runs, overlapping starts, service failures and lock handling.
   A signed-in but locked Mac still fails the existing visible-browser gate.
5. Acceptance: manually start both sources, leave the run unattended except
   for any CAPTCHA, and verify automatic reporting without chat interpretation;
   then deliberately interrupt a run and demonstrate recovery without duplicated
   or lost observations. A CAPTCHA-blocked run stays pending until resolved.
   This is assisted access, not proof of fully autonomous marketplace access.

External notifications need an agreed destination before activation. No messages
or marketplace/workbook corrections are implied by this service target.
Reliable unattended completion through marketplace CAPTCHA is unproven; a timer
and a healthy local API cannot establish that capability.

## Поисковая сессия и глубина

С 2026-10-06 по умолчанию проверяются до пяти страниц каждого поискового
фильтра. Поиск использует отдельную приватную сессию каждой площадки,
без cookies и кэша обычного Chrome и этапа осмотра карточек. Внутри поиска
состояние сохраняется между страницами и фильтрами ради ручной CAPTCHA;
следующий цикл начинает новую сессию. Подробнее:
[контракт и границы проверки](SEARCH_SESSION_AND_DEPTH_2026-10-06.md).

В видимом Avito-прогоне с ручным ожиданием CAPTCHA сетевой тайм-аут не
завершает фильтр. Статус `waiting_navigation` означает, что текущая страница
сохранена в работе: восстановите доступ и обновите эту же вкладку один раз
(Cmd+R). Автоматических повторных обновлений нет. Закрытая вкладка или её
контекст заменяется с открытием того же адреса; при отсутствии доступа цикл
продолжает ждать. Уже собранные страницы не пропадают и не собираются заново.
XHR с HTTP 200 не подтверждает восстановление: нужен свежий основной документ
с нужным адресом и параметрами. Если после CAPTCHA надпись «Проверка пройдена,
перенаправление…» остаётся дольше минуты, появляется подсказка ручного Cmd+R;
сама подсказка не прерывает ожидание и не подтверждает успешную проверку.

HTTP 439 Avito при включённом ручном ожидании также оставляет текущую
страницу в работе, даже если на ней нет распознаваемого виджета CAPTCHA.
Продолжение требует свежего HTTP 200 основного документа нужной страницы.
Если ручное ожидание отключено или закончилось, отказ остаётся технической
ошибкой; последующие фильтры этой площадки не открываются автоматически.
Ошибка уже собранного фильтра учитывается в сводке даже тогда, когда сверка
ссылок не оставила ни одной цели для сопоставления.

Для карточки Avito с визуально похожими кириллическими буквами в `unique_id`
связь с реестром допускается только по точному ASCII ID из фида, при
одновременном уникальном совпадении `AvitoId`, текущей ссылки маркетинга и
открытой активной карточки. Дубли и противоречия запрещают такую связь.
Исходный ID карточки и предупреждение о смешанных алфавитах сохраняются;
описание объявления и таблица маркетинга автоматически не исправляются.

## Обход активных карточек

С 15.10.2026 прямая проверка сохраняет описание без обрезки: `card.description`
и совместимое поле `description_excerpt` содержат весь текст основного блока
объявления. `description_capture` фиксирует длину, SHA-256, селектор и отсутствие
программной обрезки. Доступная кнопка раскрытия внутри описания нажимается один раз;
после раскрытия ожидается стабильность всего текста, включая его конец.
Ожидание доступности кнопки и завершения раскрытия не ограничено тайм-аутом.
Неудачное раскрытие сохраняет прочитанное и даёт `content_incomplete`, а не подтверждение
полноты. ID в нормально загруженном тексте может отсутствовать: ожидание не
превращает неизвестный идентификатор в подтверждённый. Старые артефакты с лимитом
1 800 символов автоматически не восстанавливаются и не перезаписываются.

Правила размещения, уточнённые владельцем 2026-10-09:

- «Актуально» плюс ссылка на площадку означает, что размещение ожидается.
- Пустая ссылка означает «размещение не заявлено» и не создаёт обязательную
  прямую проверку или незавершённую задачу. Некорректная непустая ссылка
  требует сверки.
- Если публикации нет в полном свежем каталоге, результат — «объявление
  неактивно на площадке». Причину (снятие, блокировка, перевыкладка) каталог
  сам по себе не устанавливает. Неполный каталог и ошибки доступа оставляют
  статус неопределённым. Все актуальные машины остаются в реестре.
- Фактически активные карточки каталога проверяются, даже если маркетинг
  ещё не содержит их ссылок. Их позиции доступны в отчёте по ID.
- Активная карточка, не найденная при завершённом поиске первых пяти
  страниц, получает «5+». Это обозначение проверенной глубины, а не
  доказательство, что карточка находится на шестой странице или дальше.
- Различие алфавитов в полученном тексте карточки не доказывает ошибку ID
  фида или исходного описания. В отчёте отдельно показываются сырой текст,
  точный ID фида и место наблюдения.

Полный цикл сначала собирает поисковую выдачу по всем включённым фильтрам,
без посещения кабинета дилера и карточек. Сохранённые результаты находятся
в `cycles/<cycle_id>/search_collection.json` внутри каталога evidence.
Затем свежий каталог, проверка ID и сверка ссылок определяют актуальные
публикации. После фиксации реестра цикла позиции сопоставляются с этими
публикациями по сохранённой выдаче: второй поисковый обход не запускается.
Если параметры фильтра изменились, результат остаётся технической ошибкой,
а не используется для другого поиска. Отсутствие в первых пяти страницах
само по себе не означает, что объявление снято.

С 2026-10-07 обычный цикл открывает только публикации, найденные в свежем
каталоге дилера. Старые ссылки, отсутствующие в нём, остаются в очереди сверки
без автоматического посещения. Данные одного открытия активной карточки
используются для сверки ID и итоговой проверки содержания; ошибки также
сохраняются при повторном использовании. Порядок и границы:
[ACTIVE_CATALOGUE_CYCLE_2026-10-07.md](ACTIVE_CATALOGUE_CYCLE_2026-10-07.md).

## Controlled retry

### Search-only follow-up after an access block

The host runner accepts a comma-separated list of canonical Avito filter keys
with `--search-only`. It checks only those filters, shares one private search
context for CAPTCHA state, and saves separate evidence without a database cycle,
catalogue revisit or card reread. Unknown and repeated keys are refused.

With operator waiting enabled, HTTP 403/429 without visible CAPTCHA now keeps
the same page pending. A fresh successful main-frame document is required before
continuation; an XHR, stale HTTP 200, or previous page cannot release the wait.
Earlier hits and page numbering are retained. There is no timed skip or reload loop.

Verified follow-ups can be bound to a saved report via
`app.service.search_followup.save_search_followup`. The separate committed bundle
records the original report hash and validates page screenshots, HTTP status,
filter scope and capture time. Readback updates only dated search positions;
original cycle status, identity, prices and source evidence remain unchanged.
Invalid supplements are ignored with a visible evidence warning.

### Checkpoint recovery after an interrupted run

For runs started after the 2026-10-28 checkpoint implementation, the host runner
accepts a separate operation (replace the UUID and retain the original engines,
page limit and routing mode):

```bash
SELLER_PREFLIGHT_PAGES=10 CATALOGUE_LAZY_LOAD_MAX_BATCHES=10 \
./scripts/run_monitoring_host_macos.sh \
  --diagnostic-routing --engines auto_ru,avito --pages 5 \
  --captcha-wait-seconds -1 --pace normal \
  --resume-cycle <interrupted-cycle-uuid>
```

The host first terminally records abandoned ledger rows under the existing lock.
Recovery then creates a linked new attempt; it does not overwrite the parent
cycle, report or evidence. The registry, feeds and dealer catalogues are read
again. Completed searches and full active-card observations can be reused only
with intact image manifests, identical filter scope/settings/scraper code and
capture times no more than six hours old. The six-hour bound is a recovery
freshness limit, **not** a timeout on a running CAPTCHA wait. Changed catalogue
prices invalidate a saved card. Blocked, unknown, removed and truncated card
results are not reused as verified active observations.

Checkpoints are under `cycles/<id>/recovery/`, each with a hash-verified completion
marker. Results inherited by a recovery attempt are copied with their original
provenance so another interruption does not lose them. A missing or damaged
individual checkpoint requires a real check of that operation. A missing,
expired or incompatible parent manifest refuses recovery before source/browser
work, rather than silently starting a full scan. Cycles completed before the
checkpoint implementation are not retroactively resumable.

The report, marketing summary and progress log identify recovered observations.
Replayed searches preserve their original `observed_at` and do not count as an
additional consecutive absence. Completed filters/cards are not re-opened;
an interrupted filter may repeat its already visited pages. A full browser
interruption/recovery acceptance run is still required; offline tests do not
establish current marketplace access.

Read-only verification against an existing saved cycle (uses copies and the
historical snapshot clock; no live calls, source writes or DB operations):

```bash
PYTHONPATH=src:. .venv312/bin/python scripts/check_recovery_saved.py \
  --cycle-id <saved-cycle-uuid> --evidence-dir artifacts/evidence \
  --output-dir artifacts/recovery_validation
```

### Fresh full retry

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

Видимый Mac host-runner ждёт ручного прохождения CAPTCHA на Auto.ru и Avito:
сохраняет снимок, показывает вкладку и сообщение «Требуется действие».
При включённом ожидании код не перезагружает страницу и не нажимает кнопки.
Скрытый (уже решённый) виджет CAPTCHA в DOM не считается открытой проверкой:
автоматического обновления он не вызывает, и скрытый текст не блокирует
валидную страницу выдачи. Классификация страницы идёт по видимому тексту:
заметки избранного/сайдбара и скрытые маркеры не создают «пустую выдачу».
Параметр `--captcha-wait-seconds` задаёт ожидание: `180` по умолчанию,
`0` отключает его, `1..600` ограничивает время, `-1` ждёт до решения или Ctrl+C.
При `0` сохранён прежний режим одного обычного обновления при явных маркерах
CAPTCHA в разметке.

На Avito страница HTTP 429 «Доступ ограничен: проблема с IP» с кнопкой
«Продолжить» также остаётся открытой для оператора. Программа продолжит
только после HTTP 200 основного документа, исчезновения проверки и двукратного
подтверждения исходного адреса. Восстановление выдачи только через XHR/замену
DOM без основной навигации не принимается за успех: произвольный ответ 200
не документ. Если видимой проверки уже нет, а документ всё ещё отдаёт отказ,
ожидание публикует причину `manual_refresh_required` и подсказку
`captcha_manual_refresh_hint`: обновите ЭТУ ЖЕ вкладку (Cmd+R). Подсказка
повторяется с heartbeat каждые 30 секунд; автоматического обновления нет.
Живая причина «зависания» этим не подтверждается — требуется прогон с логом
`wait_reason`. Обычная ошибка HTTP без приглашения к проверке остаётся
технической неполнотой. Контейнер и headless-режим человека не ждут.
Приватный контекст площадки сохраняется внутри одного цикла; следующий цикл
начинается с нового контекста. Живая успешная проверка Avito ещё не подтверждена.

Во время ручного ожидания журнал и dashboard получают состояние каждые
30 секунд: проверка ещё открыта, открыт другой адрес, ожидается HTTP 200,
страница стабилизируется или требуется ручное обновление (XHR-only
восстановление). Если каталог уже виден, но программа ожидает HTTP 200,
оператор может вручную обновить эту же вкладку (Cmd+R). Автоматического
обновления нет. Для брендового каталога Avito удаление из адреса служебных
`sellerId` и `s=profile_search_show_all` допустимо: продавца определяет
исходный путь `/brands/...`. Другие значения `s` (например, сортировка
`s=104`), поиск, модель, гео и параметры страницы не теряются и не
принимаются как потерянные. Другой продавец и потеря параметров поисковой
выдачи не принимаются.

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
