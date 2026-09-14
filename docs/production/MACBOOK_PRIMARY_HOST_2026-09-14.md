# MacBook primary-host decision — 2026-09-14

## Decision

По решению владельца основной эксплуатационный контур A1 Monitoring — текущий
MacBook, а не MSI / Windows 11. Это меняет целевую среду и порядок работ, но
**не** является результатом M7-приёмки и не утверждает, что расписание уже
работает в production.

Windows-скрипты и Windows-инструкция остаются в репозитории как резервный
портируемый handoff. Их статическая проверка и отсутствие живого MSI-прогона
не блокируют MacBook acceptance; если Windows снова станет рабочим host, для
него потребуется отдельная приёмка с нуля.

## Целевой MacBook-контур

- один видимый Google Chrome в активной пользовательской сессии macOS и его
  отдельный persistent profile;
- Docker Desktop с локальными app, PostgreSQL и backup, доступными только через
  loopback;
- `NETWORK_PROFILE=local_browser` как provenance запуска, но не как доказательство
  VPN-маршрута;
- VPSUS остаётся включённым. ChatGPT/Codex и Auto.ru/Avito проверяются только по
  согласованному split-tunnel, без отключения VPN, reconnect или изменения bypass
  правил без отдельного решения владельца;
- `SCHEDULER_ENABLED=false` в web-контейнере: контейнер не получает доступ к
  GUI-сессии, профилю Chrome и состоянию VPSUS.

Подтверждённые stage-facts и незакрытые границы зафиксированы в
[stage gate](STAGE_GATE_2026-09-14.md),
[VPN gate](VPN_GATE_2026-09-14.md) и
[M7 acceptance](ACCEPTANCE_M7.md). Никакой новый marketplace cycle этим решением
не запускается.

## Плановое выполнение — отдельный gate

Планируемая, но ещё не принятая автоматизация состоит из двух macOS-скриптов:

- `scripts/run_monitoring_host_macos.sh` — ровно один cautious cycle через
  видимый host Chrome, без вложенного `--watch`;
- `scripts/register_monitoring_launchagent_macos.sh` — per-user GUI
  `LaunchAgent` `com.silvercatsc.a1monitoring.interactive-cycle` с ежедневным
  `StartCalendarInterval`, `RunAtLoad=false` и `KeepAlive=false`.

Регистратор по умолчанию должен только показать план. Регистрация LaunchAgent
допустима только после явного `--apply`, не выполняет scan при регистрации и
не должна запускать цикл в фоне без вошедшего пользователя. LaunchAgent — не
LaunchDaemon: он не предназначен для Session 0, lock-screen обхода или обхода
CAPTCHA. Его живое выполнение и восстановление после user log-in пока не
подтверждены.

Скрипты поставлены как **static-only contract**: их наличие, shell/Python
проверки и plan-only вывод не являются доказательством, что launchd уже может
открыть Chrome или получить доступ к проекту. Поскольку проект расположен на
Desktop, отдельный MacBook gate обязан проверить TCC / Privacy & Security для
Terminal, Google Chrome и launchd-процесса. Нельзя автоматически выдавать Full
Disk Access, снимать блокировку экрана или менять TCC-настройки ради этой
проверки.

## Безопасный host preflight — отдельное доказательство готовности

Предусмотренный режим:

```bash
./scripts/run_monitoring_host_macos.sh --preflight
```

Он проверяет, что есть корректный незаблокированный console GUI user, Docker
app/db/backup доступны в ожидаемом локальном контуре, а локальный readiness
отвечает. Этот режим **не создаёт** `MonitoringCycle`/`ScanRun`, не открывает
Chrome, не обращается к Auto.ru или Avito, не меняет VPSUS/VPN и не выполняет
проверку фактического browser-control через TCC.

Поэтому успешный `--preflight` — лишь evidence готовности host. Он не доказывает
маршрут VPSUS, доступ Chrome к проекту/площадкам, корректность парсера или M7
acceptance; для этого остаются отдельные gates ниже.

### Проверенный запуск

На текущем Mac/stage `--preflight` был фактически выполнен 2026-09-14 с
`15:38:07Z` по `15:38:08Z`. Privacy-safe status зафиксировал
`preflight_succeeded`, `kernel_fcntl` lock и
`execution_model=readiness_only_no_cycle`. Это подтверждает именно
GUI/unlocked-host readiness с локальными `app`/`db`/`backup`; мониторинговый
cycle, Chrome и marketplace traffic этим запуском не создавались.

Проверка не меняет перечень обязательных gates: нужен отдельный evidence
VPSUS/browser route, controlled cycle, TCC/Desktop результат и owner review
перед применением LaunchAgent. Не интерпретировать этот короткий preflight как
проверку Chrome, VPN или production scheduler.

До отдельного MacBook gate оператор использует только ручной one-cycle запуск;
`local_scan.sh --watch` остаётся foreground-loop открытого Terminal и не
считается production scheduler. Нельзя запускать `--watch`, host-runner и
container scheduler одновременно.

## Что нужно подтвердить до включения расписания

1. VPSUS split-tunnel в обычном Chrome для ChatGPT, Auto.ru и Avito без изменения
   VPN-конфигурации.
2. Один ручной MacBook controlled cycle с видимым Chrome и разбором результата.
3. Отсутствие другого worker и `SCHEDULER_ENABLED=false`.
4. Plan-only проверка LaunchAgent и явный owner review перед `--apply`.
5. После первого trigger — один cycle ID, privacy-safe status/log и отсутствие
   параллельного запуска; `partial` не повторяется автоматически.
6. Явный TCC/Desktop результат для GUI-пользователя, Chrome и launchd; при
   lock screen, отсутствии console user или отказе TCC scan не начинается.

Пока хотя бы один пункт не подтверждён, это план дальнейшей поставки, а не
запущенная production-автоматизация.
