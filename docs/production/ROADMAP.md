# Production roadmap

Этот документ — рабочий план поставки. Коммит и push выполняются после завершения
каждой реперной точки с тестами и обновлением документации. Публикация данных и
живой обход площадок не являются частью обычного CI.

| Реперная точка | Версия | Состав | Gate |
| --- | --- | --- | --- |
| M0. Baseline | `0.9.0+baseline` | Актуальная история, тесты, документация, удалённый GitHub | Репозиторий синхронизирован, secrets не добавлены |
| M1. Honest cycle | `0.10.0` | Итог поиска и карточек, единый final status, stop-gates, цикл/evidence manifest | Невозможно получить `completed` при провале обязательной стадии |
| M2. Stable identity | `0.11.0` | Vehicle, Offer, SourceRecord, история ID и ручное подтверждение перевыкладки | Машины без VIN не сливаются, новый offer не теряет историю |
| M3. Evidence collectors | `0.12.0` | Контракты Auto.ru/Avito, fixtures CAPTCHA/blocked/layout, Drom/site после приёмки | Каждому выводу соответствует сохранённое доказательство |
| M4. Reconciliation | `0.13.0` | Цена, НДС, статус, ghost/missing offers, очередь review | Выводы объясняются пользователю без чтения кода |
| M5. Operator release | `0.14.0` | Ролевой дашборд, обратная связь, отчёт по исключениям | Маркетинг и РОП обрабатывают очередь самостоятельно |
| M6. Operations | `0.15.0` | Retry/resume, MacBook schedule, backup/restore, метрики, allowlist публикации | Подтверждено восстановление после сбоя и бэкапа |
| M7. Acceptance | `1.0.0` | MacBook shadow-run, контрольная выборка, rollback | Результаты подтверждены на живом цикле и владельцем данных |

## M1 завершён в коде: Honest cycle

Выполнено в `0.10.0`:

1. Итог поиска и прямых карточек объединён в единую честную сводку.
2. Добавлен `cycle_id`, журнал `monitoring_cycles` и immutable roster manifest.
   Идентификатор сохраняется в import snapshot, discovery, reconciliation и scan run;
   в наблюдение попадает как provenance, а не как новый бизнес-факт.
3. Частичная проверка больше не получает `completed`; локальный runner возвращает
   ненулевой код при неполноте.
4. Head-table/site audits, AI work units и Ouroboros fail closed при смешанном
   `cycle_id`; partial core-цикл не запускает их вообще.
5. `/status/cycles` показывает partial/failed попытки отдельно от latest run;
   явное `removed` у прямой карточки не считается техническим сбоем проверки.

Офлайн-gate включает regression tests для CAPTCHA/429, blocked/unknown direct card,
пустого или обрезанного импорта, неполной поисковой выдачи, пустой/неизвестной
конфигурации источника и смешанного AI-пакета. Живой обход в этот gate не входит.

Открытые ограничения после M1:

1. Управляемый retry/resume остаётся M6: M1 намеренно создаёт новый цикл после
   сбоя, чтобы не приписать поздние данные старому manifest.
2. Веб-очередь для маркетинга и РОП — M5; сейчас операторский JSON endpoint уже
   не скрывает incomplete evidence за latest run.

## M2 завершён в коде: stable identity

Выполнено в `0.11.0`:

1. Введены append-only `Vehicle`, `Offer`, `SourceRecord` и `OfferVehicleLink`;
   `Listing` сохранён как совместимая проекция для прежних наблюдений.
2. Миграция `20260914_0008` создаёт Vehicle для каждого старого Listing и
   candidate-связи прежних валидных external ID, без переписывания истории.
3. VIN или единственный уже связанный external ID разрешают строку; похожие поля
   no-VIN машины не используются для автоматического объединения.
4. Ручная смена ссылки формирует подтверждённую историю перевыкладки; contested
   candidate другой машины отклоняется, но её история не удаляется.

Офлайн-gate включает чистую/наследуемую миграцию, две одинаковые no-VIN машины с
разными Offer ID, повторный импорт и подтверждение перевыкладки. Живая проверка
вёрстки или доступа площадок в него не входит.

## M3 завершён в коде: evidence collectors

Выполнено в `0.12.0`:

1. PNG выдачи, exact-card и direct-card получают рядом privacy-bounded `evidence.v1`
   manifest с SHA-256, размером, временем, source/purpose и hash URL.
2. Если screenshot страницы, целевой карточки или прямой карточки не сохранился,
   collector возвращает `evidence_missing`, а не business fact.
3. Добавлены проверяемые manifests API и удаление companion manifest вместе с
   истёкшим PNG.
4. Введены fixtures Auto.ru CAPTCHA/unknown layout и Avito blocked/empty плюс
   Playwright regression для отсутствующего exact-card screenshot.

Drom и новый сайт не подключались. Fixtures доказывают код и контракт, но не
доказывают текущую вёрстку или доступность платформ.

## M4 завершён в коде: reconciliation

Выполнено в `0.13.0`:

1. `/reconciliation/review-queue` и dashboard агрегируют последнюю сверку в
   понятные findings с фактом, действием, priority и ссылкой на evidence.
2. Active direct-card сопоставляется с реестром по цене, VIN и году; для Avito
   отдельно выделяются отсутствующий и противоречивый статус НДС.
3. Снятый Offer, отсутствующая ссылка, candidate перевыкладки и technical
   incompleteness не смешиваются. Снятие карточки не называется продажей машины.
4. Свежий `DealerListingCandidate`, которого нет среди active links, формирует
   `ghost_offer` только с M3 page evidence; иначе виден technical finding.

Очередь read-only, не меняет links и не подтверждает business status. Live
контроль площадок и owner approval по findings не выполнялись.

## M5 завершён в коде: operator release

Выполнено в `0.14.0`:

1. При `AUTH_ENABLED=true` Basic-auth выдаёт серверную роль из `ADMIN_*` и
   secret-backed `AUTH_USERS_JSON`; клиентское имя в форме не может повысить
   полномочия.
2. Маркетинг создаёт feedback и ведёт его до `fixed`; РОП подтверждает только
   `fixed → confirmed`; admin/operator управляют полным разрешённым lifecycle.
3. Подтверждение или ручная смена marketplace-link доступны только
   admin/operator. Наличие ticket не подтверждает связь Offer и Vehicle.
4. M4 finding создаёт ticket только пока finding остаётся текущим; сохраняются
   `reconciliation_id`, `finding_code`, автор и история переходов.
5. `/reconciliation/exceptions-report` сопоставляет текущие findings и tickets
   read-only. В нём feedback остаётся процессом человека, а finding — фактом.

Офлайн-gate проверяет роли, миграцию, lifecycle и устаревший finding. Реальная
настройка пользователей, живое принятие ролей и доступ к площадкам не выполнялись.

## M6.1 реализован в коде: controlled retry и операции

Новый retry запускает **новый** цикл с `retry_of_cycle_id`: он не может
дописать новые факты к старому roster manifest. Операторский retry идёт только
через `make retry-cycle CYCLE_ID=<UUID>` и Mac host runner; scheduler не является
альтернативой и `SCHEDULER_ENABLED=true` отвергается. Детали —
[M6 Operations](OPERATIONS.md).

M6 остаётся открытым до проверенных backup/restore, publication allowlist и
целевой runtime-приёмки. Офлайн-test не считается восстановлением production.

## M6.2–M6.3 реализованы в коде: backup integrity и publication allowlist

Backup пишет SHA-256 companion, restore-test сравнивает dump/Alembic/ключевые
таблицы во временной БД. Public export требует свежий owner allowlist и создаёт
только aggregate-only HTML.

Current Mac/stage technical gate 14.09.2026 закрыт: the latest controlled deploy
is at app 0.14.0 / Alembic 20260914_0011; checksum backup and isolated
restore-test passed. Historical backup старой схемы также прошёл restore-test.
Evidence: [STAGE_GATE_2026-09-14.md](STAGE_GATE_2026-09-14.md).

M6 не получает version 0.15.0 до owner review aggregate-only export, MacBook
проверки backup-age/controlled retry и отдельного решения о включении
пользовательского LaunchAgent. Export, Pages publish и внешняя публикация не
выполнялись. Windows/MSI остаётся резервным handoff: его runtime не является
обязательным gate для MacBook release.

## M7 начат на текущем Mac, но не завершён

Единый [M7 acceptance runbook](ACCEPTANCE_M7.md) задаёт live stop-gates для
VPSUS, backup/restore, MacBook shadow-run, gated LaunchAgent и owner rollback
decision. Решение владельца о MacBook как primary host зафиксировано в
[MACBOOK_PRIMARY_HOST_2026-09-14.md](MACBOOK_PRIMARY_HOST_2026-09-14.md).
На текущем Mac зафиксированы read-only состояние VPN/маршрутов, backup/restore и
одностраничные probes Auto.ru/Avito; это ещё не закрывает direct split-tunnel.
Evidence записано в VPN_GATE_2026-09-14.md и LIVE_PROBE_2026-09-14.md.

Уточнение owner от 14.09.2026 усилило Avito scope: все шесть фильтров теперь
требуют `/moskva/`, `radius=0`, `searchRadius=0` и включённый
`localPriority=1` (каталог v6). Старый цикл с `localPriority=0` не является
доказательством корректной московской выборки. Fresh v6 shadow-run verified the
Avito contract but correctly ended `partial` pending link review; details:
[AVITO_SELECTED_RADIUS_2026-09-14.md](AVITO_SELECTED_RADIUS_2026-09-14.md).

M7 нельзя закрыть в CI или автоматическим агентом: нужны утверждённая owner
контрольная выборка, полноценный MacBook shadow-run с ручной сверкой, реальные
role accounts и owner decision по включению либо отказу от LaunchAgent.
Windows-приёмка требуется только если владелец снова выберет Windows как
рабочий host.

## Рабочий ритм

1. Уточнение scope и критериев реперной точки.
2. Минимальное изменение кода и tests в одном коммите.
3. Ruff, unit/integration tests, проверка документации.
4. Commit и push в `main` после успешного gate.
5. Краткая запись в `CHANGELOG.md` и переход к следующей реперной точке.

Если тест показывает неясность данных или поведение живой площадки не подтверждено,
это фиксируется как известный риск, а не объявляется готовой функцией.
