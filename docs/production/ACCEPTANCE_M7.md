# M7: production acceptance и rollback

Статус: **начат на Mac/stage, не принят**. Fresh v6 shadow-cycle completed with
the correct Avito selected-radius mode but has a valid `partial` outcome; see
[AVITO_SELECTED_RADIUS_2026-09-14.md](AVITO_SELECTED_RADIUS_2026-09-14.md).
Этот runbook не разрешает сам запуск;
он задаёт порядок приёмки после отдельного подтверждения владельца. Ни unit tests,
ни статическая проверка Windows, ни сохранённый исторический запуск не закрывают M7.

Mac/stage deployment evidence: [DEPLOYMENT_2026-09-14.md](DEPLOYMENT_2026-09-14.md).

## Результат M7

Версия `1.0.0` возможна, только если есть независимые записи пяти фактов:

1. рабочий split-tunnel для ChatGPT, Auto.ru и Avito на этом компьютере,
   с owner-approved policy и доказательством для IPv4 и IPv6;
2. успешный checksum backup и isolated restore-test на целевой среде;
3. shadow-run с контрольной выборкой и evidence по площадкам;
4. если owner включит расписание — один MacBook host-runner / LaunchAgent trigger
   в активной пользовательской сессии без параллельного цикла;
5. owner sign-off состава ролей, ограничений и rollback-плана.

Провал любого пункта даёт `blocked` или `failed`; он не заменяется повтором
кода/CI и не разрешает 1.0.

## Gate 0 — условия и запреты

- Нет незакоммиченных изменений: `git status --short` пуст.
- Зафиксированы commit SHA, версия Alembic, `APP_ENV`, `NETWORK_PROFILE`, версии
  Chrome/Python/Docker и время МСК.
- `AUTH_ENABLED=true`, настроен admin и отдельные реальные stage-учётные записи
  marketing / sales_director / operator; пароли не попадают в журнал.
- Есть одобренная контрольная выборка: ссылка/ID, VIN или внутренний ID, фильтр,
  ожидаемая страница, цена/год/НДС и ожидаемое состояние карточки. Она хранится
  локально, не в public export.
- Запрещены изменение marketplace-кабинетов, обход CAPTCHA, чужие cookies,
  запись в Google Sheets и внешний publish. Acceptance только наблюдает.

## Gate 1 — VPSUS split-tunnel

Текущее правило «исключение» не считается доказательством маршрута: разные версии
VPN-клиента трактуют его как bypass или как proxy-route. VPSUS остаётся включённым;
не выключать VPN и не закрывать ChatGPT/Codex как workaround для monitoring. До
изменения переключателя, bypass-правила или reconnect требуется явное подтверждение
владельца.

Current Mac routing evidence is recorded in
[VPN_GATE_2026-09-14.md](VPN_GATE_2026-09-14.md). It is a configuration and
basic reachability result, not a replacement for the remaining M7 gates.

Current Mac marketplace probes are recorded in
[LIVE_PROBE_2026-09-14.md](LIVE_PROBE_2026-09-14.md). They verify adapters on
one page each without creating a monitoring cycle.

Avito selected-radius configuration and its runtime evidence are recorded in
[AVITO_SELECTED_RADIUS_2026-09-14.md](AVITO_SELECTED_RADIUS_2026-09-14.md).
A fresh shadow-run must use this v6 filter catalogue.

Отдельно подтверждена одновременная функциональная доступность в обычном Chrome:
при оставленном включённым VPSUS загрузились ChatGPT, Auto.ru и Avito, в адресной
строке которого был виден канонический selected-radius URL. Во время этой проверки не менялись
настройки VPSUS, не выполнялись reconnect, scheduler или marketplace scan.
Это подтверждает только пользовательскую проверку доступности, а не доказывает
direct/bypass rule либо фактический egress каждого домена; Gate 1 и M7 остаются
открытыми. Полная privacy-safe запись — в
[VPN gate](VPN_GATE_2026-09-14.md).

До проверки owner фиксирует intended path (`VPN exit` или `direct physical
connection`) для каждого сервиса, его нужных subdomains и обеих IP families.
После этого оператор фиксирует screenshot/текст настроек и выполняет один
контролируемый доступ в обычном Chrome:

| Цель | Ожидаемый маршрут | Критерий |
| --- | --- | --- |
| ChatGPT | согласованный VPN/direct маршрут | действующая сессия и новая страница без сетевой ошибки |
| `auto.ru` | direct browser route для Monitoring | главная/контрольный поиск открываются без auth/CAPTCHA обхода |
| `avito.ru` | direct browser route для Monitoring | главная/контрольный поиск открываются без auth/CAPTCHA обхода |

Проверка не доказывает стабильность площадок и не решает CAPTCHA. Ошибка сети,
401/403/429 или CAPTCHA остаётся `technical_error`; не отключать защиту площадки
ради зелёного результата.

## Gate 2 — восстановление

Mac/stage status: **completed** on 2026-09-14 at Alembic `20260914_0011`.
The fresh backup and isolated restore matched the required control tables;
evidence is in [STAGE_GATE_2026-09-14.md](STAGE_GATE_2026-09-14.md). A Windows
execution is not part of the current MacBook acceptance scope.

На той же целевой машине, без параллельного импорта:

```bash
./scripts/backup_now.sh
./scripts/restore_test.sh
```

Windows backup/restore остаётся отдельной процедурой резервного handoff; его
результат понадобится только при возвращении Windows в эксплуатационный scope.
Для любого host сохранять только `BACKUP_OK`/`RESTORE_OK`, время, checksum и
revision Alembic — не dump, не `.env`. Restore создаёт и удаляет временную БД;
если его результат не `RESTORE_OK`, production rollout останавливается.

## Gate 3 — Mac shadow-run

1. Запустить видимый Chrome и одну осторожную полную проверку, без AI:
   `./scripts/local_scan.sh --engines auto_ru,avito --pages 3 --pace cautious`.
2. Сохранить `cycle_id`, final status и M3 evidence manifests.
3. Сверить вручную контрольную выборку: source, timestamp, ID, страницу,
   direct-card, цену, год, VIN/внутренний ID и Avito НДС, когда он применим.
4. Убедиться, что blocked/CAPTCHA/timeout не названы непоказом или продажей;
   снятая карточка остаётся статусом Offer.
5. Проверить M4/M5: finding объясним, ticket не меняет link автоматически,
   marketing не подтверждает `fixed`, а РОП не перескакивает статусы.

`scripts/live_acceptance.sh` намеренно выведен из этого пути: его прежний
container `POST /scan` не может управлять или подтверждать видимый host Chrome,
TCC/Desktop либо маршрут VPSUS основного MacBook. Скрипт теперь fail-closed и
не читает `.env`, не вызывает API и не начинает обход. Он не является fallback
для этой приёмки.

Для admission нужен `completed` только если правила M1 действительно соблюдены;
`partial` допустим как корректный отрицательный результат реализации, но не
закрывает M7. The 2026-09-14 v6 run is precisely such a correct negative result:
the Avito contract is verified, while link reconciliation remains open.

## Gate 4 — MacBook host-runner / LaunchAgent (только если нужен график)

Основной MacBook уже является целевым host, но автоматическое выполнение на нём
ещё не принято. Не включать `SCHEDULER_ENABLED=true`: web-контейнер не может
надёжно управлять видимым Chrome в GUI-сессии macOS.

Сначала допустим только host-only preflight:

```bash
./scripts/run_monitoring_host_macos.sh --preflight
```

Он проверяет GUI/unlocked console, Docker `app`/`db`/`backup` и local readiness,
но не создаёт cycle, не открывает Chrome, не обращается к площадкам, не меняет
VPN и не проверяет TCC browser-control. Его результат нужен как техническое
evidence host, но сам по себе не закрывает даже этот Gate и не разрешает scan.

После этого выполнить plan-only проверку
`scripts/register_monitoring_launchagent_macos.sh --at HH:MM`. Только после
отдельного owner review допустим явный `--apply`. Ожидаемый per-user GUI
LaunchAgent `com.silvercatsc.a1monitoring.interactive-cycle` должен запускать
ровно один `scripts/run_monitoring_host_macos.sh` с видимым Chrome, без вложенного
`--watch`, `RunAtLoad` или `KeepAlive`-повтора. Регистрация не должна выполнять
marketplace scan.

После первого trigger отдельно доказать, что:

- пользователь вошёл в GUI-сессию macOS; это не LaunchDaemon и не lock-screen
  обход;
- TCC / Privacy & Security разрешает GUI-пользователю, Chrome и launchd-процессу
  доступ к проекту на Desktop; никакое permission не выдаётся автоматически;
- `alembic upgrade head` применён, dashboard открывается на loopback, а
  `SCHEDULER_ENABLED=false`;
- видимый Chrome и один cautious controlled cycle не исчерпывают память и не
  зависают;
- mutex/статус runner не допускают второй цикл, а `partial` не вызывает
  автоматический retry;
- retry создаёт новый `cycle_id` с `retry_of_cycle_id`, а не меняет прежний
  manifest.

Скрипты и LaunchAgent в этом gate — static-only поставка кода; их живой
GUI-trigger, TCC/Desktop доступ и поведение при lock screen не заявлены
проверенными до записи отдельного MacBook evidence.

## Gate 5 — owner decision и rollback

Owner сверяет все материалы и выбирает один результат: `accepted`, `accepted with
known limitations`, `blocked` или `rejected`. Для `accepted` должны быть указаны
роли, approved filters, частота, хранение evidence, реакция на CAPTCHA и
технические ошибки.

Rollback выполняется только из последнего verified backup в отдельную БД; затем
проверяются Alembic revision и ключевые количества до переключения. Не применять
destructive Alembic downgrade, `docker compose down -v` или переписывание
manifest как «быстрый rollback».

## Windows/MSI — резервный, не обязательный gate

Windows/PowerShell скрипты сохранены для переносимости, но Windows/MSI больше не
является целевой production-средой. Его статическая проверка не переносится на
MacBook и не требуется для M7. Если владелец снова выберет Windows, необходимо
отдельно повторить Gates 0–4 на той машине: Docker/backup/restore, VPSUS,
видимый Chrome, ручной cycle и интерактивный Task Scheduler без container
scheduler.

## Артефакт приёмки

Заполнить локальный, непубличный протокол: commit SHA, время/ОС, routes VPN,
checksum/revision restore, cycle IDs, контрольная выборка, screenshots/evidence
hashes, найденные deviations, решение owner и дата повторной проверки. В Git
коммитить только обезличенный итог без VIN, URL, токенов, dump и персональных
feedback.
