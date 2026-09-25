# Локальный VPN admission contract — MacBook, 2026-09-14

> Исторический контракт M7 на 14.09.2026. С 25.09.2026 повседневный MacBook
> runner использует [постоянную локальную политику и текущий статус VPSUS](VPN_OPERATIONAL_POLICY_2026-09-25.md).
> Указанные ниже требования к 24-часовой записи и порядок её чтения runner
> описывают прежний gate; они не являются текущей инструкцией обычного запуска.
> Подтверждение фактических маршрутов IPv4/IPv6 для M7 остаётся отдельной задачей.

## Назначение и граница

Этот contract — короткоживущая локальная attestation владельца перед полным
`local_browser` cycle на основном MacBook. Он нужен, чтобы host-runner
**fail-closed** остановился до recovery, Chrome, импорта и записи
`MonitoringCycle`, если нет свежего owner decision о допустимых маршрутах
ChatGPT, Auto.ru и Avito.

MacBook — единственный выбранный владельцем production host platform; это не
закрывает его M7 runtime gates. Windows/MSI не получает этот admission как
переносимое доказательство и остаётся deliberately fail-closed fallback до
отдельной живой приёмки.

Запись не является сетевым probe, VPN-конфигурацией или доказательством egress.
Она не читает и не меняет VPSUS, не reconnect-ит VPN, не открывает браузер и не
контактирует с площадками. Фактическое read-only evidence и незакрытые ограничения
маршрутизации остаются в [VPN gate](VPN_GATE_2026-09-14.md); полный порядок
приёмки — в [M7](ACCEPTANCE_M7.md).

Нельзя создавать такую запись «для зелёного статуса». Сначала владелец лично
подтверждает intended route и оператор проверяет три сервиса в обычном Chrome при
оставленном включённым VPSUS. Если доказательство маршрута, IPv4/IPv6 или policy
неполны, записи `approved` быть не должно: full cycle остаётся заблокированным.
Текущий [VPN gate](VPN_GATE_2026-09-14.md) фиксирует unresolved IPv6 attribution,
поэтому на момент этой записи корректная attestation ещё не может быть выдана.

## Место и права доступа

Канонический локальный путь, исключённый из Git:

```text
artifacts/vpn_admission/attestation.json
```

Каталог обязан принадлежать текущему GUI-пользователю macOS и иметь ровно `0700`;
regular file — ровно `0600`. Symbolic link, group/world-readable file, extended macOS ACL,
непринадлежащий пользователю каталог и файл больше 8 KiB отвергаются. Host runner
может безопасно подготовить только пустой private directory:

```bash
.venv312/bin/python -m app.service.vpn_admission \
  --prepare-directory artifacts/vpn_admission
```

Команда не создаёт attestation, не подтверждает маршрут и не меняет VPN. После
owner-authored создания файла оператор проверяет права без вывода содержимого:

```bash
ls -lde artifacts/vpn_admission artifacts/vpn_admission/attestation.json
```

Ожидаемые mode — `drwx------` и `-rw-------` **без** суффикса `+`: любой
extended ACL намеренно отвергается. Не прикладывать JSON, снимки
VPSUS, URL, cookies или результаты этой команды к Git, issue либо публичному
отчёту.

## Точная схема owner attestation

Top-level object обязан содержать **ровно** пять ключей:

| Ключ | Допустимое значение |
| --- | --- |
| `schema_version` | integer `1` |
| `status` | string `approved` |
| `issued_at_utc` | UTC timestamp `YYYY-MM-DDTHH:MM:SSZ` |
| `expires_at_utc` | UTC timestamp того же формата |
| `services` | object ровно с `chatgpt`, `auto_ru`, `avito` |

Каждый объект внутри `services` обязан содержать **ровно** `route`, `ipv4` и
`ipv6`. `route` может быть только `vpn_exit` или
`direct_physical_connection`; оба family-поля обязаны быть строкой `verified`.
Нельзя добавлять `owner`, URL, домены, IP, screenshot path, комментарий, token или
export VPN-конфигурации: лишний ключ делает schema invalid и защищает файл от
превращения в хранилище чувствительных данных.

По этой же privacy-причине JSON не содержит имени или подписи владельца. Он
подтверждает только соблюдение локального operational gate; owner decision и его
исходное browser evidence хранятся отдельно, локально и не коммитятся.

Структура ниже показывает имена и типы; угловые скобки являются намеренно
невалидными placeholders, а не готовым разрешением:

```json
{
  "schema_version": 1,
  "status": "approved",
  "issued_at_utc": "<UTC time of the owner decision>",
  "expires_at_utc": "<UTC time no later than 24 hours after issued_at_utc>",
  "services": {
    "chatgpt": {
      "route": "<vpn_exit or direct_physical_connection>",
      "ipv4": "verified",
      "ipv6": "verified"
    },
    "auto_ru": {
      "route": "<vpn_exit or direct_physical_connection>",
      "ipv4": "verified",
      "ipv6": "verified"
    },
    "avito": {
      "route": "<vpn_exit or direct_physical_connection>",
      "ipv4": "verified",
      "ipv6": "verified"
    }
  }
}
```

`expires_at_utc` должен быть позже `issued_at_utc`, но не более чем на 24 часа;
`issued_at_utc` не может быть более чем на 5 минут в будущем. Предыдущая запись
не продлевается автоматически. После expiry, изменения policy, reconnect или
смены VPSUS-правил владелец повторяет фактическую проверку и создаёт новую
attestation; runner никогда не исправляет её сам.

## Порядок admission

1. Владелец фиксирует ожидаемый route каждого из трёх сервисов и подтверждает
   IPv4/IPv6 по read-only browser evidence из M7 Gate 1.
2. Оператор создаёт private directory, затем owner-authored JSON по точной схеме
   с ограниченным сроком действия. Не копировать example и не угадывать route.
3. Полный `scripts/run_monitoring_host_macos.sh` проверяет запись после local
   readiness, но до recovery, Chrome и нового cycle. Он также удерживает и
   передаёт дочернему процессу проверяемый inherited Mac host-lock FD.
   `--preflight` намеренно не читает запись и не создаёт cycle.
4. Missing, expired, insecure или invalid record даёт стабильный отказ
   `VPN_ADMISSION_REFUSED`; runner сохраняет только privacy-safe phase и не
   начинает marketplace traffic. `partial` также не вызывает автоматический
   retry.

`LOCAL_BROWSER_HOST_ADMISSION=true` устанавливается host-runner для своего
дочернего запуска, но сам по себе больше недостаточен. Центральный app cycle
дополнительно требует проверяемый inherited Mac host-lock FD, удерживаемый тем же
host runner. Просто выставить boolean, передать путь к файлу или положить валидную
attestation недостаточно: container/local non-Mac process fail-closed, даже если
у него есть оба таких артефакта. Это operational anti-accidental boundary,
связывающий child с живым runner, а не утверждение о защите от намеренных действий
того же локального пользователя. Ни один из маркеров не доказывает VPN-маршрут.

## Что этот contract не разрешает

- не подтверждает, что текущая route rule VPSUS действительно исполняется;
- не разрешает менять, выключать, reconnect-ить VPN или обходить CAPTCHA;
- не заменяет owner approval, controlled shadow-run, TCC/Desktop gate,
  reconciliation review или M7 sign-off;
- не делает Windows fallback принятым: Windows остаётся deliberately fail-closed
  резервным handoff согласно [решению о MacBook host](MACBOOK_PRIMARY_HOST_2026-09-14.md).

Возможность запустить host runner появляется только после всех применимых M7
gates. Этот contract добавляет безопасный stop-rule, а не доказательство того, что
production monitoring уже принят.
