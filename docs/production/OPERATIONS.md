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

## Открытые части M6

- backup/restore сценарии для macOS и Windows теперь проверяют checksum, dump,
  revision Alembic и контрольные количества, но их нужно выполнить на целевой
  среде и зафиксировать результат, а не заменить офлайн-тестом;
- publication allowlist должен быть включён до любого публичного export;
- мониторинг backup-age и controlled retry на Windows/MSI требуют отдельной
  живой приёмки.

Пока эти пункты не закрыты, M6 не повышает релиз до `0.15.0` и не заменяет M7.
