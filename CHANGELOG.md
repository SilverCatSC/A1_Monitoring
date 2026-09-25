# Журнал изменений

## M7.12 — простой список автомобилей — 2026-09-25

- Экран «Автомобили» переделан в компактный список по образцу каталога:
  название, год, наличие, цена, подтверждённый ID и краткое состояние двух
  площадок видны без технических подробностей.
- Несопоставленные строки маркетинга показаны в этом же списке; закрытая
  старая ссылка не названа продажей автомобиля. Скриншот объявления явно
  отличается от фотографии машины.
- Логика сверки и URL не менялись. Приёмка удобства владельцем остаётся
  открытой; ограничения и следующая задача описаны в
  `docs/production/TASK_VEHICLE_LIST_UX_2026-09-25.md`.

## M7.11 — unique_id во вкладке «Автомобили» — 2026-09-25

- Каталог и детальная страница показывают ID по площадкам из последней
  доверенной прямой проверки или однозначного ID-кандидата перевыкладки.
  Неподтверждённый или конфликтующий ID не подставляется по VIN/модели.
- Владелец подтвердил две Auto.ru перевыкладки из контролируемого цикла;
  ссылки в БД и маркетинговой таблице этим изменением не заменяются.
- Точное объяснение двух других карточек без ссылки и правила отображения ID:
  `docs/production/TASK_LISTINGS_UNIQUE_ID_2026-09-25.md`.

## M7.10 — controlled Auto.ru ID cycle and separator-row fix — 2026-09-25

- Один Auto.ru-only MacBook цикл с ID-сверкой завершился `partial`: две точные
  кандидатуры перевыкладки поставлены на ручную проверку; старые ссылки на них
  показывали «автомобиль продан», новые карточки активны с тем же placement ID.
  Avito не посещался, URL автоматически не менялись.
- В отчёте найдена ложная ошибка ID на текстовой строке-разделителе Auto.ru
  фида. Следующие циклы пропускают только такие строки, сохраняя исходные
  номера строк и отказ для настоящей автомобильной строки без ID.
- Итоги, ограничения покрытия и оставшаяся ручная работа записаны в
  `docs/production/M7_AUTORU_ID_CYCLE_2026-09-25.md`. M7 не принят.

## M7.9 — повседневный VPN-допуск MacBook — 2026-09-25

- Обычный host-runner теперь проверяет постоянную приватную политику владельца
  (`ChatGPT → VPN`, `Auto.ru/Avito → direct`) и текущее подключение VPSUS.
  Истечение прежней 24-часовой attestation не мешает запуску; неподходящая
  политика, права файла или отключённый VPN по-прежнему останавливают цикл.
- Gate обозначает только operational readiness, не фактический egress IPv4/IPv6
  и не M7 acceptance. Живой marketplace-цикл не запускался из-за отдельного
  ограничения браузерного доступа к Avito. Проверены unit-тесты и preflight.

## M7.8 — очередь ручного подтверждения по ID — 2026-09-25

- Однозначный `republication_candidate` из ID-сверки теперь попадает в
  существующую очередь оператора, если VIN фида связывает ровно одну активную
  запись, прежняя ссылка требует проверки, а новая не занята другим автомобилем.
- Найденная карточка и снимок связываются с проверкой; старые подсказки только
  по модели для того же URL заменяются объяснением по ID. Подтверждение остаётся
  ручным и аудируемым, URL в реестре не меняется автоматически.
- При неполной или неоднозначной связи находка остаётся только в отчёте.
  Живой цикл не запускался: owner VPN admission истёк, а прямой доступ к Avito
  через разрешённый браузер ранее был остановлен политикой безопасности.

## M7.7 — operator review for ID reconciliation — 2026-09-24

- Добавлен внутренний экран сверки ID с кандидатами перевыкладки, строками
  фида и ссылками на сохранённые снимки; отчёт цикла доступен read-only через
  API. Снимок отдаётся только после проверки manifest и SHA-256.
- Повторно проверен gate живого запуска: owner VPN admission истёк, а доступ к
  Avito через разрешённый браузер ранее был остановлен политикой безопасности.
  Живой цикл не выполнялся; новый этап остаётся выключенным по умолчанию.
- Локальный MacBook app пересобран; readiness и новый экран ответили успешно,
  показывая отсутствие живого отчёта без подмены его синтетическими данными.

## M7.6 — bounded placement-ID stage in the full cycle — 2026-09-24

- Полный MacBook-цикл получил опциональный read-only этап: снимок трёх фидов,
  прямое открытие новых карточек каталога в пределах лимита, сверка ID и
  неизменяемый отчёт по `cycle_id`.
- Пример фида прочитан через CSV-export: 38 Auto.ru ID, 23 Avito new и 9 Avito
  used; это проверка источника, не живых карточек.
- Этап выключен по умолчанию. При включении неполный каталог, лимит, отсутствие
  evidence или недоступный фид делают итог цикла `partial`; ссылки и площадки
  не изменяются. Живая приёмка ещё не выполнена.

## M7.5 — operational matching across known mixed-script typos — 2026-09-24

- По read-only проверке `avito-feed-new!A9:C9` для присланного владельцем
  объявления подтверждена связь латинского `Id` с номером карточки через
  `AvitoId=8176281881` в фиде; живая карточка этим не проверена.
- Сверка использует уникальный AvitoId как дополнительное подтверждение, но
  не как единственный идентификатор: при отсутствующем или неисправимом ID
  описания результат `platform_id_candidate` требует проверки. После
  перевыкладки новый AvitoId может ещё не попасть в фид; точный ID или узкий
  кириллический визуальный аналог продолжает находить новую карточку.
  Конфликт ID описания с AvitoId, дубликат AvitoId или отсутствие direct-card
  evidence блокируют решение.
- При единственном ID в фиде и подтверждённой активной карточке известные
  кириллические двойники в буквенном префиксе допускаются для read-only
  определения `link_current`/`republication_candidate`. Результат помечен
  `id_match_basis=visual_alias`, а сырой ошибочный ID сохранён в warning.
- Тот же ограниченный допуск действует при ошибке в фиде; дубликаты считаются
  после приведения и блокируют привязку. Публикационный аудит фида остаётся
  строгим и не разрешает отправлять некорректный ID на площадку.
- Отсутствие или неоднозначность ID в фиде, неподтверждённая карточка и ошибки
  в цифровых сегментах не обходятся. Автоматической записи URL по-прежнему нет.

## M7.4 — review-only visual ID candidate — 2026-09-24

- Смешение кириллицы и латиницы в первых четырёх символах ID теперь даёт
  подсказку только при единственном точном ID в фиде и наличии evidence.
  Исходный ID остаётся неподтверждённым; цифровые сегменты не исправляются,
  автоматической перевязки URL нет.
- Проверки статуса карточки, цены, года и позиции не останавливаются из-за
  ошибочного ID. Сам ID остаётся задачей на исправление у маркетинга.

## M7.3 — Avito ID comparison and mixed-script error — 2026-09-24

- Read-only сверка ID расширена на оба фида Avito; дубликат между `new` и
  `used` блокирует решение о перевыкладке. Ссылки автоматически не меняются.
- В предоставленном владельцем описании Avito обнаружена кириллическая `М`
  вместо латинской `M` в ID. Парсер сохраняет исходный заявленный ID для
  диагностики, но не использует его как доказательство идентичности.
- Ошибка передана 24 сентября в чат Битрикс24 `А1 АВТО // Маркетинг`;
  исправление на площадке ещё не подтверждено.

## M7.2 — Auto.ru feed-to-card identity decision — 2026-09-24

- Добавлена read-only сверка `unique_id`/`action` фида с ID из открытых
  карточек продавца и актуальной ссылкой реестра по VIN. Она отличает
  перевыкладку от корректной ссылки, дубликата и недостаточных доказательств.
- Смена ссылки намеренно не включена: ещё нет контролируемого обхода всех
  карточек каталога с подтверждённым ID и полным evidence. Avito исключён до
  фактического появления ID в описаниях.

## M7.1 — owner placement-ID contract — 2026-09-15

- Зафиксирован 22-символьный placement ID: код марки/модели, тип/подтип, год
  автомобиля, дата размещения и порядковый номер. Все три owner examples
  покрыты fail-closed parser/formatter tests.
- ID признан идентификатором размещения, не физического автомобиля: при новой
  продаже он может измениться. Автоматическая перевязка URL требует отдельного
  stable `a1_vehicle_id`/VIN/складского ID и журнала публикаций.
- По read-only аудиту реального marketing workbook зафиксирован маппинг:
  Auto.ru `unique_id`, Avito `Id` — customer placement ID; Avito `AvitoId` —
  ID площадки. Добавлен fail-closed pre-publication audit для malformed,
  duplicate и перепутанного `Id`/`AvitoId`; пустой `feed-dromru` не получил
  выдуманного контракта.
- Auto.ru начал указывать ID в `CardDescriptionHTML`; `autoru-feed-all!B2:C39`
  даёт 29 активных `show` и 4 скрытых `hide` ID, все соответствуют контракту.
  Extractor остаётся read-only: автоматическая смена ссылки запрещена до
  controlled sample точного совпадения ID на открытой карточке. Avito ещё не
  входит в этот контур.

## M7 status map and republication decision — 2026-09-14

- Добавлен единый owner-facing status map с целью системы, evidence по
  контурам, измеримыми остаточными gates и объяснением `missing_link` /
  `removed` / `review_required`.
- Зафиксирован следующий product milestone: deterministic mapping
  `VIN/внутренний ID → текущий URL` для безопасного автоматического обновления
  ссылок после перевыкладки; совпадение только по модели/цене/году намеренно
  не считается достаточным.

## M7 — MacBook controlled-cycle evidence — 2026-09-14

- После owner authorization и strict private VPN admission MacBook выполнил
  ровно один cautious cycle `527c4ad1-20fd-4967-a814-31ce75fea124` через
  visible host Chrome. VPSUS не менялся и не переподключался; Auto.ru и Avito
  были уже заданы как direct-mode exceptions.
- Runner корректно завершил цикл `partial` без автоповтора: технических ошибок
  `0`, но `22` ссылок требуют сверки и `14` direct-card записей неполны.
  Все 14 неполных direct-card связаны с отсутствующей валидной прямой ссылкой
  в реестре; остальные 8 non-verified записей корректно классифицированы как
  снятые/закрытые либо требующие review. Это корректный review result, не
  доказательство completed/M7 acceptance.
- В `48de30f` исправлен Mac recovery path: он использует закрытый loopback DSN
  Docker из `.env`, а не container-only `localhost:5432`; повторный run прошёл
  recovery до browser scan. Privacy-safe evidence:
  [M7_CONTROLLED_CYCLE_2026-09-14.md](docs/production/M7_CONTROLLED_CYCLE_2026-09-14.md).

## M6.12 — macOS unlocked-session semantics — 2026-09-14

- На разблокированном текущем Mac `CGSSessionScreenIsLocked` отсутствует, а
  `kCGSSessionOnConsoleKey=true` и `kCGSessionLoginDoneKey=true`. Gate теперь
  различает именно эту валидную unlocked-сессию от нечитабельного состояния,
  сохраняя отказ при явном `CGSSessionScreenIsLocked=true`.
- Проверено реальным `run_monitoring_host_macos.sh --preflight`: host, services
  и loopback readiness успешно прошли без Chrome, VPN mutation, cycle/recovery
  или marketplace traffic.

## M6.11 — VPSUS dual-stack reachability evidence — 2026-09-14

- Read-only VPSUS UI now visibly confirms `avito.ru` and `auto.ru` in mode
  «Напрямую»: the client describes that mode as bypassing VPN over the ordinary
  internet connection. VPSUS remained connected; no rule was changed and no
  reconnect occurred.
- Fresh unauthenticated checks reached ChatGPT, Auto.ru and Avito over both
  IPv4 and IPv6 (`403` from ChatGPT after TLS; `200` from both marketplaces).
  This proves dual-stack service reachability, not public egress attribution;
  owner policy declaration and the short-lived VPN admission record are still
  required before a full cycle.

## M6.10 — MacBook lock-screen fail-closed correction — 2026-09-14

- Обнаружена и устранена расходимость IOKit-сигналов: при
  `IOConsoleLocked=false` активная GUI-сессия могла одновременно иметь
  `CGSSessionScreenIsLocked=true`. MacBook host-runner и LaunchAgent registrar
  теперь требуют `IOConsoleLocked=false` и подтверждённую активную GUI-сессию;
  отсутствие per-session lock-key принимается только при такой сессии, потому
  что именно так текущая macOS представляет разблокированное состояние.
- На фактически заблокированной сессии `run_monitoring_host_macos.sh --preflight`
  завершается до поднятия сервисов с
  `HOST_RUNNER_REFUSED reason=screen_locked_or_state_unavailable`. Это локальное
  отрицательное evidence: Chrome, VPN и marketplace cycle не запускались.

## M6.9 — MacBook-only execution boundary и VPN admission — 2026-09-14

- Полный marketplace cycle и controlled retry теперь проходят только через
  `run_monitoring_host_macos.sh`: GUI/unlocked-user, kernel lock, host readiness,
  recovery и видимый Chrome остаются в одном entrypoint. Прямой CLI/API/container
  путь не может выдать себя за host-runner только переменными конфигурации.
- Перед full cycle нужен локальный owner-authored `artifacts/vpn_admission/`
  record: строгая schema v1 для ChatGPT/Auto.ru/Avito, 24-hour TTL, `0700/0600`,
  отсутствие extended ACL и fail-closed проверка до recovery/Chrome/записи цикла.
  Он не является доказательством egress и не меняет VPSUS.
- `SCHEDULER_ENABLED=true`, Mac extended pipeline, raw headless diagnostic,
  recurring `--watch` и Windows marketplace entrypoints намеренно отказываются.
  Legacy/cloud profiles остаются читаемыми для исторических отчётов, но больше не
  могут начать новый cycle; Windows — только непринятый setup/dashboard handoff.
- M7 остаётся **не принят**: валидная attestation не выпускалась, VPSUS IPv6/
  egress proof, controlled shadow-run, реальные роли и TCC/LaunchAgent gates
  всё ещё требуют отдельного owner evidence. Никакой VPN change, live scan или
  scheduler registration этим изменением не выполнялись.

## M6.8 — production-auth hardening и post-deploy stage verification — 2026-09-14

- `fdd3ef4` fail-closed проверяет production-конфигурацию, обязательный roster
  из четырёх ролей и минимальную длину секретов; cloud override не может
  унаследовать local stage/no-auth профиль.
- Повторный deploy на основном MacBook/stage подтвердил loopback readiness и
  Mac-host preflight без создания monitoring cycle, обращения к площадкам или
  изменения VPSUS.
- Это compatibility evidence локального stage, а не приёмка реальных ролей,
  Basic-auth, split-tunnel или M7.

## M6.7 — проверенный Mac preflight и восстановление dashboard — 2026-09-14

- На Mac/stage применён `25c55fe`: при Alembic `20260914_0011` и версии
  приложения `0.14.0` loopback `/api/v1/health`/`/api/v1/ready`, `/api/v1/dashboard` и
  `/api/v1/dashboard/settings` подтвердили успешный ответ. Исправление сохраняет
  существующую PostgreSQL-метку `review_required` для нового observation state,
  не переписывая исторические значения.
- Первый фактический `./scripts/run_monitoring_host_macos.sh --preflight`
  завершился `preflight_succeeded` (2026-09-14T15:38:07Z–15:38:08Z) с
  kernel `fcntl` lock и моделью `readiness_only_no_cycle`. Он проверил host
  readiness без создания monitoring cycle, Chrome, marketplace traffic,
  изменения VPSUS/VPN либо TCC browser-control.
- Это устраняет техническую dashboard-преграду и даёт evidence готовности
  Mac-host, но M7 по-прежнему **не принят**: остаются проверка VPSUS в обычном
  Chrome, controlled marketplace cycle, TCC/Desktop gate, owner review перед
  LaunchAgent `--apply` и evidence первого scheduler trigger.

## M6.6 — MacBook primary-host decision — 2026-09-14

- По решению владельца основной production host изменён с планируемого MSI/Windows
  на текущий MacBook. Это изменение scope, не M7 acceptance и не новый live scan.
- Runbooks теперь требуют видимый Chrome в активной macOS GUI-сессии и VPSUS
  split-tunnel без выключения VPN или ChatGPT/Codex. `SCHEDULER_ENABLED` остаётся
  false: container scheduler не получает доступ к host Chrome.
- Планируемый macOS host-runner/LaunchAgent остаётся plan-only до явного
  `--apply`, отдельного owner review и evidence первого trigger; автоматический
  retry `partial` запрещён. Windows PowerShell/Task Scheduler сохранён как
  непринятый fallback, а не обязательный release gate.
- Для host-runner зафиксирован безопасный `--preflight`: GUI/unlocked console,
  Docker app/db/backup и local readiness проверяются без цикла, Chrome,
  marketplace traffic, VPN-изменений или доказательства TCC browser-control.
  Это host evidence, не M7 acceptance.
- Finder `.command` launchers теперь имеют документированный preflight
  executable-bit; пока он не подтверждён, оператор использует Terminal и не
  считает запуск двойным кликом принятым.

## M6.5 — Windows interactive host-runner contract — 2026-09-14

- Added a Windows host-runner contract for exactly one cautious cycle in a
  signed-in interactive desktop; it is mutex-protected, refuses Session 0 and
  records a privacy-safe operational status without automatic marketplace retry.
- Task registration is plan-only until explicit `-Apply`; the proposed
  `\A1Monitoring\InteractiveCycle` is Interactive-only, ignores concurrent
  triggers and has `RestartCount=0`. Container `SCHEDULER_ENABLED` remains
  false because it cannot operate host Chrome.
- Current runbooks use the VPSUS split-tunnel contract: ChatGPT/Codex stays
  available while Auto.ru/Avito use approved direct browser rules. Windows/MSI
  runtime remains unaccepted; no external Windows execution was performed.

## M6.4 — interruption-safe cycle ledger — 2026-09-14

- `KeyboardInterrupt` and normalized `SIGTERM` now terminalize a registered
  monitoring cycle as `failed`, preserving its immutable roster manifest.
- Added lock-aware `recover-open-cycles` CLI and protected recovery API. It can
  only recover abandoned `preparing`/`running` rows after the complete-cycle
  lock is idle; actor, timestamp and recovery reason are retained.
- Package metadata now matches runtime version `0.14.0`. Local `.env` and
  evidence/Chrome-profile directories are owner-only on the current Mac/stage.
- The one historical open cycle was recovered through the new application route,
  not direct SQL; the manifest remains immutable and operations now reports zero
  active cycles.

## M7 pre-acceptance — v6 shadow cycle and link-review state — 2026-09-14

- Fresh controlled cycle `70c56c6b-bddf-4403-bb04-df4788458266` used the v6
  Avito selected-radius catalogue. Avito completed 6/6 filters, 12 catalogue
  pages and 10 expected hits with zero marketplace technical errors.
- The whole cycle correctly stayed `partial`: 22 current links require
  reconciliation and 14 direct-card records are incomplete. No stale link was
  presented as an absence, sale or a clean M7 acceptance.
- Added `review_required` observation state and migration `20260914_0011`.
  Seller-link reconciliation is now an operator-action result rather than a
  false `technical_error`; genuine CAPTCHA, HTTP, timeout and parser failures
  remain technical errors. Immutable observations from earlier cycles are not
  rewritten.

## M7 pre-acceptance — Avito selected radius — 2026-09-14

- По уточнению владельца все шесть канонических Avito-фильтров требуют
  `/moskva/`, `radius=0`, `searchRadius=0` и включённый
  `localPriority=1` (`Сначала в выбранном радиусе`). Обновлены шесть
  действующих URL; 38 назначений и идентичность фильтров сохранены.
- Автоскан не принимает URL других городов как московские hits. Новое
  правило проверено в локальном Chrome, stage и профильных tests.
- Предыдущий partial и остановленный до обхода фильтров cycle остаются
  диагностикой; свежий shadow-run с v6 необходим для M7.

## M6.1 — controlled retry и операционные метрики — 2026-09-14

- Повтор `partial`/`failed` цикла создаёт новый `MonitoringCycle` с
  `retry_of_cycle_id`; старый roster manifest и наблюдения не переписываются.
- Добавлен CLI `retry-cycle`, защищённый операторский маршрут и
  `/status/operations`; scheduler coalesces пропуски и блокирует параллельный
  job. Миграция `20260914_0010` добавляет retry provenance.
- Backup/restore, allowlist публикации и живая приёмка Windows/Mac остаются
  незавершёнными частями M6; версия не повышалась до `0.15.0`.

## M6.2 — проверяемое резервирование — 2026-09-14

- Новые dump получают companion SHA-256 в backup-volume; автоматический backup
  чистит dump и его checksum вместе.
- macOS и Windows restore-test проверяют checksum, читаемость custom dump,
  revision Alembic и количества ключевых таблиц только во временной БД.
- Windows-скрипты проверены статически. Ни backup, ни restore не запускались на
  текущем Mac/MSI, поэтому восстановление production пока не подтверждено.

## M6.3 — allowlist публичной сводки — 2026-09-14

- Старый export внутренних dashboard-страниц заменён одной aggregate-only
  страницей без VIN, URL, карточек, evidence и текста/авторов feedback.
- Для каждого export требуется короткоживущий owner allowlist вне Git; output
  отказывается смешиваться с неизвестными прежними файлами и получает checksum.
- Не было owner approval, export, Pages deploy или публикации в Bitrix. M6 не
  объявляется production-ready до их отдельных проверок.

## M6 live-gate — schema deployment stop — 2026-09-14

- Scoped backup выполнился и записал SHA-256 companion в backup-volume.
- Restore-test корректно остановился до изменения рабочей БД: запущенный app
  имеет `0.9.0` / Alembic `20260909_0006` и не содержит `monitoring_cycles`.
  Current source требует head `20260914_0010`; container не пересобирался и не
  мигрировался без отдельного deploy-разрешения.

## M5 — operator release — 2026-09-14

- Добавлены серверные роли `admin`, `operator`, `marketing`, `sales_director`.
  При включённой аутентификации роль определяется только `ADMIN_*` и
  secret-backed `AUTH_USERS_JSON`; отправленное браузером имя не повышает права.
- Маркетинг создаёт ticket и ведёт его до `fixed`, РОП подтверждает только
  `fixed → confirmed`, а admin/operator выполняют все допустимые переходы.
  Подтверждение и смена marketplace-link ограничены admin/operator.
- Ticket для M4 finding создаётся только для текущей сверки и хранит
  `reconciliation_id` / `finding_code`; добавлен read-only отчёт по исключениям.
- Добавлена аддитивная миграция `20260914_0009`; версия повышена до `0.14.0`.
  Офлайн tests не являются живой приёмкой ролей, площадок или Windows.

## M4 — объяснимая сверка Offer — 2026-09-14

- Добавлена read-only очередь Offer в API и dashboard: цена, VIN, год, НДС Avito,
  снятые/missing/candidate/ghost Offer и техническая неполнота имеют факт,
  приоритет, действие и ссылку на доказательство.
- Ghost Offer требует page evidence + manifest свежего dealer discovery; active
  direct-card без M3 manifest не становится бизнес-фактом.
- Снятие объявления формулируется как статус Offer, а не продажа автомобиля.
  Очередь не изменяет links, Vehicle/Offer или исходные таблицы.
- Версия повышена до `0.13.0`; добавлены regression tests для сверки и evidence
  dealer candidate. Живая приёмка площадок не выполнялась.

## M3 — контракт доказательств — 2026-09-14

- Для новых PNG выдачи, точной карточки и direct-card добавлен companion manifest
  `evidence.v1`: SHA-256, размер, UTC, площадка/назначение и hashes URL без
  дублирования raw URL, VIN или HTML.
- Сбой screenshot страницы или целевой карточки завершает обход как
  `evidence_missing`; direct-card без изображения не подтверждает active/removed.
- API может вернуть проверенный manifest; при очистке истёкшего PNG удаляется и
  companion JSON. Подмена файла обнаруживается integrity-проверкой.
- Добавлены fixtures CAPTCHA/blocked/unknown layout/empty и Playwright regression
  для обязательного exact-card screenshot. Версия повышена до `0.12.0`.
- Живой обход площадок и обход CAPTCHA не выполнялись.

## M2 — стабильная идентичность — 2026-09-14

- Добавлены `Vehicle`, `Offer`, `SourceRecord` и датированная связь
  `OfferVehicleLink`; прежний `Listing` и его `listing_id` в наблюдениях не
  переписываются.
- Миграция `20260914_0008` backfill-ит по одному Vehicle на исторический Listing
  и candidate-связи валидных URL. Откат удалением истории запрещён: используется
  проверенный backup.
- Без VIN импорт больше не использует марку/модель/год/цену как ключ слияния.
  Одинаковые машины с разными offer ID остаются разными Vehicle.
- Ручное подтверждение ссылки сохраняет `operator_confirmed`, автора, причину,
  старый superseded offer и отклонение конкурирующего candidate, без скрытого
  удаления второй машины.
- Версия повышена до `0.11.0`; добавлены tests identity и migration backfill.
  Живая приёмка площадок по-прежнему не выполнялась.

## M1.5 — закрытие offline gate — 2026-09-14

- Пустой импорт теперь закреплён regression test: снимок уходит в карантин с
  `cycle_id`, а последний валидный реестр не деактивируется.
- M1 закрыт как code/documentation milestone. Live acceptance, controlled resume
  и Windows-приёмка не объявляются завершёнными и остаются в M6/M7.

## M1.4 — операторская очередь циклов — 2026-09-14

- Добавлен `/status/cycles`: partial и failed попытки видны отдельно от latest
  scan вместе с причиной или ошибкой, поэтому не исчезают из очереди оператора.
- Явное снятие прямой карточки признано завершённым результатом проверки, а не
  техническим сбоем. Оно по-прежнему не трактуется как продажа автомобиля.
- Пустая или неизвестная конфигурация источников блокируется до внешнего обхода.

## M1.3 — fail-closed AI packet — 2026-09-14

- API и локальный runner выдают точный `cycle_id`; status endpoint возвращает
  ledger конкретного цикла, а scan endpoint умеет выбрать только его runs.
- Head-table и site audits получают ID как явный аргумент. AI work units, staged
  Hermes review и Ouroboros сверяют этот ID; разнородные `latest`-файлы отвергаются.
- Полные macOS/Windows сценарии не запускают дополнительные аудиты и AI после
  partial core-цикла. Пустая или неизвестная конфигурация marketplace source также
  блокируется до создания `ScanRun`.

## M1.2 — ledger и immutable roster — 2026-09-14

- Добавлена миграция `20260914_0007`: таблица `monitoring_cycles` и `cycle_id`
  в import snapshot, каталогах продавца, сверках и поисковых прогонах.
- Полный цикл создаёт durable ledger до импорта. После принятого импорта он один
  раз сохраняет privacy-bounded roster manifest в `artifacts/.../cycles/<id>`;
  VIN и исходные URL в него не попадают.
- При сбое ledger получает `failed`; при успешном завершении хранит итоговую
  сводку. Поздние результаты не приклеиваются к старому циклу.
- Версия приложения повышена до `0.10.0`; добавлены тесты correlation, immutable
  manifest и миграции. Живая приёмка площадок и resume не выполнялись.

## M1.1 — честный итог цикла — 2026-09-14

- Добавлен единый итог цикла: техническая ошибка или неполнота прямых карточек
  больше не может завершить прогон со статусом `completed`.
- Локальный runner возвращает code `2` для частичного цикла с раздельными
  причинами поиска, сверки ссылок и прямых карточек.
- В прогрессе различены «поиск завершён, проверяются карточки» и финальный статус
  всего цикла; добавлены регрессионные тесты blocked direct cards.
- Зафиксированы production contract и roadmap с gates от baseline до `1.0.0`.
- Проверено: 217 tests passed, Ruff, документационные ссылки и `git diff --check`.

## Документация и локальный перенос — 2026-09-14

- Проект перемещён в `/Users/filaret/Desktop/Monitoring`, без дублирования моделей
  и артефактов. Старый путь сохранён как совместимая символическая ссылка.
- Обновлены корневые документы; добавлены эксплуатационные инструкции, структура,
  поэтапный алгоритм с Mermaid, аудит реализации и план развития.
- Исторические документы сохранены в `docs/archive`; удалены четыре пустых
  каталога-заготовки. Код бизнес-логики и схема БД не менялись.
- Добавлен офлайн-калькулятор стоимости LLM API, датированные первичные тарифы,
  допущения, сценарии и тесты денежной арифметики. API не подключён.
- Добавлен единый `docs/PROJECT_DOSSIER.md`: описание продукта, стек, алгоритм,
  текущий статус, ограничения, запуск, стоимость API и встроенный листинг точки
  запуска `local_scan.py` с ссылкой на канонический исходник.
- Зафиксированы ограничения P0: итоговый статус прямых карточек, целостность
  цикла AI, идентичность без VIN и отсутствие безопасного публичного экспорта.
  Это результаты аудита, не заявления об исправлении этих дефектов.
- Локальные незакоммиченные изменения сохранены. Commit/push, live-обход площадок
  и приёмка Windows в рамках переноса не выполнялись.
- После переноса: 214 тестов passed, Ruff для `src tests scripts tools` passed,
  `git diff --check` passed; 40 Markdown-документов, 130 внутренних файловых ссылок
  без отсутствующих целей. External URLs и anchors линтер документации не проверяет.
- Read-only readiness подтвердил доступность приложения и БД; импорт Python
  указывает на `Monitoring`. Синтетически воспроизведён ложный `completed` при
  blocked direct cards — исправление бизнес-логики оставлено отдельной задачей.

## 0.9.0 — 2026-09-10

- По решению владельца основной рабочий контур перенесён на MSI / Windows 11.
  Mac остаётся резервным до подтверждённого переноса истории.
- Добавлен нативный Windows-контур Hermes, Ouroboros и CPU llama.cpp с закреплёнными
  версиями, vision-проектором и отдельными локальными профилями.
- Полный Windows-запуск выполняет Chrome/Docker и локальную 9B-модель разными фазами.
  Перед моделью служебный Chrome и контейнеры останавливаются, а запуск блокируется
  при запасе RAM менее 7500 МБ; после анализа дашборд возвращается.
- Windows-код проверен статически; общий набор вырос до 207 тестов. Живой прогон на
  MSI остаётся обязательным приёмочным этапом.

- После поиска последовательно открываются все валидные прямые карточки Auto.ru и
  Avito. Состояние URL, причина снятия, извлечённые цена/VIN/год/наличие/НДС и
  viewport-снимок сохраняются в истории сверки.
- В терминале и локальном прогрессе видна отдельная стадия `КАРТОЧКА N/всего`.
  Блокировка площадки останавливает дальнейшие прямые обращения к этому источнику.
- Раздел «Автомобили» стал одностраничным отчётом отдела продаж. Поиск в выдаче и
  состояние прямой карточки показаны отдельно; для найденной машины указан номер
  страницы, а для снятой сохранены ссылка, причина и снимок.
- Цена, VIN, год, статус/наличие и НДС берутся из последнего локального снимка
  головной таблицы. Активные строки без связи с Monitoring, включая машины без VIN,
  не скрываются и выводятся отдельным блоком.
- Для Avito НДС извлекается из описания прямой карточки. Детерминированная сверка
  выявляет снятие и расхождения цены, VIN, года и НДС до обращения к LLM.
- Hermes получает малое текстовое задание и снимок прямой карточки по автомобилю;
  Ouroboros аудирует полноту и доказательность итогов. Последовательный режим и
  паузы сохранены. Только неудачное малое задание Hermes повторяется один раз.
- Статический экспорт для GitHub Pages переносит не только поисковые, но и прямые
  карточечные снимки.
- Проверено 204 тестами, Ruff и локальным UI-smoke. Живой no-VPN прогон новой
  стадии прямых карточек требует операторской приёмки.

- Предыдущим решением основной запуск был на MacBook Pro M2 Pro / 16 ГБ; этот режим
  теперь сохранён как резервный и как подтверждённый AI-пилот.
- Полный установщик macOS включает базовые инструменты, зависимости проекта,
  llama.cpp, локальный Hermes, Q00/Ouroboros и отдельную загрузку модели.
- Общий запуск после обхода вызывает локальную QA-проверку Hermes и сохраняет
  отдельный результат; AI не меняет наблюдения и выгружается из памяти после проверки.
- Установлены и проверены Qwen3.5-9B Q4_K_M, llama.cpp/Metal, Hermes 0.21.1,
  Q00/Ouroboros 0.54.1 и Tirith 0.4.1; облачные LLM-маршруты и телеметрия отключены.
- QA, внутренние LLM-вызовы Ouroboros и изменяющий код runtime разделены на три
  профиля Hermes. Текстовые профили не имеют инструментов; инженерный профиль
  работает с одним worker в отдельном HOME/worktree и fail-closed Tirith.
- Исправлена несовместимая передача `--max-turns` в Hermes one-shot. Добавлены
  строгая схема AI-отчёта и узкое восстановление только незакрытых JSON-контейнеров
  в конце ответа; внутреннее повреждение JSON остаётся ошибкой.
- Добавлен локальный vision-проектор Qwen3.5 и реальное распознавание сохранённых
  снимков карточек. AI-пакет разделяется по одному автомобилю и одному снимку;
  запросы выполняются последовательно с паузами, одним slot и четырьмя потоками.
- Внутреннее рассуждение отключено для малых сверок и ограниченно включено только
  для ранжирования готовых замечаний. Итог собирается детерминированно из сохранённых
  findings, поэтому модель не может добавить новый источник или неподтверждённый факт.
- Ouroboros получает компактный staged-отчёт и проверяет покрытие, доказательства и
  противоречия, не повторяя распознавание и первичную сверку Hermes.

- Отдельная пошаговая инструкция Windows 11: установка, локальная диагностика,
  видимый прогон, история результатов, остановка и ограничения публикации.
- Диагностика ищет Chrome тем же кроссплатформенным способом, что и мониторинг.
- Windows-запускатели останавливаются после ошибок внешних команд, проверяют
  Python 3.12 и используют порт из `.env`; вывод Python переведён в UTF-8.
- `-SkipDocker` больше не сообщает о готовности незапущенного приложения.
- Приёмка Windows на MSI и безопасной публичной выгрузки остаётся открытой.

## 0.8.1 — 2026-09-09

- Разобран реальный запуск 11:54–12:11: 21 обнаружение, 11 записанных непоказов,
  4 проблемных фильтра; подробности в docs/LIVE_RUN_REVIEW_2026-09-09.md.
- Сверка распознаёт отдельные баннеры «Этот автомобиль уже продан» и
  «Объявление снято с публикации» вне заголовка карточки.
- Из поискового разбора исключаются рекомендации и дополнительные города;
  Москва дополнительно проверяется по адресу объявления Avito. Номер позиции
  сохраняет место карточки внутри основного списка.
- Пагинация проверяется по отображаемому номеру и уникальным ID страницы.
  Повтор выдачи даёт неопределённость; короткая выдача Avito останавливается
  по явному счётчику, последняя страница — по элементам пагинации.
- Сохраняются запрошенный/конечный URL, фактический номер и ID карточек каждой
  страницы. Это позволяет разобрать последующий прогон без повторных запросов.
- Снимок карточки пропускает скрытый дубль и сохраняет причину неудачи.
- Пропущенные после сверки ссылки видны в итоге отдельного фильтра.
  pages_scanned новых запусков суммирует страницы фильтров; прежние значения
  в базе были максимумом страницы и не пересчитываются.
- Изолированные тесты не закрывают живую приёмку Zeekr Auto.ru.

## 0.8.0 — 2026-09-09

- Полный локальный цикл теперь обязательно сверяет ссылки с двумя каталогами
  Auto.ru (cars/LCV) и указанным каталогом Avito A1 до поиска.
- Одна блокировка охватывает импорт, сверку и мониторинг; ручное подтверждение
  ссылки ждёт завершения активного цикла.
- PostgreSQL-блокировки scan/discovery удерживаются на отдельном соединении:
  промежуточный commit больше не меняет соединение их освобождения.
- Новый раздел «Сверка ссылок», журнал отдельных проверок и предложения
  похожих объявлений для подтверждения человеком. Автопривязка по цене/модели запрещена.
- Новые таблицы listing_reconciliations и listing_link_overrides, миграция 0006.
  Подтверждённая локальная ссылка имеет приоритет над входной; расхождение видно в UI.
- Непроверенные, снятые и неоднозначные ссылки дают технический результат,
  а не подтверждённый непоказ. Seller membership не считается позицией в поиске.
- Достижение лимита страниц не считается полным каталогом продавца;
  повтор страниц и уход на другой адрес распознаются как неопределённость.
- Старые ссылки проверяются выборочно, до 5 на площадку за цикл, с паузами.
  CAPTCHA/401/403/429 останавливают дальнейшие обращения к этой площадке.
- Фильтр VLE заменён на group URL с tech_param=25032431; Москва/0 км сохранены.
  Индивидуальная new/group карточка больше не принимается за поисковый фильтр.
- История, цены, страницы, замечания, cautious pacing и прежние разделы сохранены.
- Полный CLI/API-цикл требует видимый Chrome; обычный запуск — local_scan.sh.
  Диагностический --probe-url остаётся отдельной проверкой без бизнес-наблюдений.
- Локальное изменение ссылки VLE основано на явном сообщении владельца, не на
  автоматическом сопоставлении или утверждении о живой доступности страницы.

## 0.7.0 — 2026-09-08

- Общая навигация и адаптивный интерфейс: обзор, автомобили, размещения,
  статистика, история, замечания, активность, настройки.
- Разделение автомобилей, текущих публикаций и повторных наблюдений.
- Статистика за 7/30/90 дней с выбором площадки, марки, VIN и архива;
  календарь по времени Москвы, выделение выходных и дней без данных.
- История изменений цены, активности и ссылок из импорта; миграция 20260908_0005.
- Новые наблюдения сохраняют ожидаемый ID объявления и версию фильтра.
  Смена ID закрывает прежний эпизод с причиной замены, а не найденного объявления;
  новая ссылка не наследует серию непоказов и старое превью.
- Обновлены локальная установка, диагностика, Makefile, фиксированные зависимости;
  добавлены два macOS .command-файла для оператора.
- Smoke стал read-only: без импорта реальных строк и без живого скана.
- Сохранены все ранее существовавшие изменения рабочего дерева. Git commit/push
  в рамках этого обновления не выполняются. Базовый commit до работ: 7eb4f22.

## 0.6.3 — 2026-09-08

- Считывание Auto.ru с верхней части страницы; гипотеза о виртуализации Zeekr
  не подтверждена отдельным контролируемым экспериментом.
- Обязательный импорт источника перед локальным сканом и повтором --watch.

## 0.6.2 — 2026-09-08

- Остановка короткой выдачи Auto.ru по числу предложений.
- Один повтор фильтра при TargetClosedError, прогресс в терминале и отчёте.

## 0.6.1 — 2026-09-07

- Щадящий локальный темп с видимыми паузами, постоянный Chrome-профиль,
  карточечные снимки и номера страниц.

Подробная история ранних решений: docs/RELEASE_STATUS.md. Записи до 0.7.0
составлены по сохранённой документации; это не замена Git-истории.
