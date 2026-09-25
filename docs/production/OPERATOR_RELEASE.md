# M5: роли, feedback и исключения Offer

Статус: реализовано в коде `0.14.0`. Этот документ описывает права в приложении,
а не фактические учётные записи сотрудников. Production-настройку и живую
приёмку ролей владелец ещё не подтверждал.

## Модель доступа

При `AUTH_ENABLED=true` приложение требует Basic-auth. Для production/M7 startup
fail-closed требует все четыре серверные роли: admin (обычно из
`ADMIN_USERNAME` / `ADMIN_PASSWORD`), а также operator, marketing и
sales_director. Пользователи из `AUTH_USERS_JSON` задаются в секретном окружении,
но не в Git, `.env.example`, отчётах или ticket:

```json
{
  "operator": {"password": "длинный-секрет", "role": "operator"},
  "marketing": {"password": "длинный-секрет", "role": "marketing"},
  "sales-director": {"password": "длинный-секрет", "role": "sales_director"}
}
```

Пароль каждого пользователя production должен быть не короче 16 символов. Для
переноса секрета использовать manager окружения/CI или локальное хранилище
секретов. Не вставлять реальные примеры в документацию, сообщения или коммиты.
`AUTH_ENABLED=false` остаётся только для stage на loopback; он не создаёт
production-доступ без входа. Проверка гарантирует наличие ролей, но не
подтверждает, что реальные люди и owner sign-off уже назначены.

| Роль | Может | Не может |
| --- | --- | --- |
| `admin` | Все разрешённые переходы feedback, controlled cycle/discovery/import, синхронизация и изменение фильтров, ручная смена и подтверждение ссылок | Автоматически признать candidate тем же Vehicle |
| `operator` | Все разрешённые переходы feedback, controlled cycle/discovery/import, синхронизация и изменение фильтров, ручная смена и подтверждение ссылок | Изменить первичный маркетинговый источник |
| `marketing` | Создать feedback; `new → checking/assigned`; `checking/assigned → fixed` | Запустить monitoring/import/discovery, изменить фильтры, подтвердить исправление или сменить ссылку Offer |
| `sales_director` | Создать feedback; подтвердить `fixed → confirmed` | Запустить monitoring/import/discovery, изменить фильтры, исправить/сменить ссылку или пропустить промежуточную проверку |

Разрешения применяются сервером к текущей роли. Поля «кто выполняет» и «кто
проверил» нужны для stage-совместимости и истории, но при включённой аутентификации
не являются источником полномочий.

При включённой аутентификации сервер возвращает `403` для roles вне
`admin`/`operator` на operational и registry-mutations: `POST /scan`, `POST
/cycle`, `POST /dealer/discover`, `POST /import`, `POST /filters/catalog/sync`,
`POST /filters` и `PATCH /filters/{id}`. Это не отменяет отдельные M7-gates для
видимого Chrome, VPSUS и controlled cycle.

Role permission не является entrypoint bypass: новый marketplace cycle принимает
только MacBook host runner с локальной VPN operational policy, подключённым
VPSUS и inherited host-lock FD.
Прямой API-вызов не может заменить этот operational gate.

## Локальные технические проверки при Basic-auth

`doctor.py`, `check_ui.py` и `build_live_agent_packet.py` продолжают работать
после `AUTH_ENABLED=true`: они читают `ADMIN_USERNAME` / `ADMIN_PASSWORD` только
внутри локального процесса, создают Basic header или browser context только в
памяти и обращаются исключительно к `127.0.0.1`, `::1` или `localhost` с явным
портом. Header, пароль и Base64-token не передаются в shell arguments, не
печатаются и не идут через redirect или удалённый `--api-url`. При включённой
аутентификации без локальной admin-пары проверка fail-closed; создавать или
подставлять секрет ради зелёного check нельзя.

## Работа с исключением

1. На «Сверка с каталогами продавца» прочитать факт, действие и evidence.
2. Нажать «Создать обращение» только для актуального finding, добавить проверяемый
   контекст и перейти в «Замечания».
3. Маркетинг назначает/проверяет/исправляет по разрешённому lifecycle; это ещё не
   подтверждает, что marketplace-card принадлежит нужному автомобилю.
4. РОП подтверждает только результат, уже переведённый в `fixed`, с комментарием.
5. Для смены URL оператор отдельно сверяет VIN, комплектацию и фото и выполняет
   M2-подтверждение. Ticket не заменяет это действие.

Создание ticket проверяет, что `reconciliation_id` и `finding_code` всё ещё есть
в текущей M4-очереди. Если finding исчез или сменился, API вернёт `409`: нужно
обновить сверку, а не прикреплять старое решение к новой реальности.

## Отчёт и контроль

`GET /api/v1/reconciliation/exceptions-report` — read-only JSON для операционной
очереди. Он содержит только текущие findings и связанные с ними tickets. Устаревший
finding не возвращается как текущий бизнес-факт даже если у старого ticket ещё
есть история. Для каждой строки видны priority, evidence-ссылка, открытые tickets,
автор, ответственный и последний переход.

Перед включением production-доступа:

1. Создать только нужные аккаунты и длинные уникальные пароли.
2. Проверить сценарии marketing, sales_director и operator отдельными учётными
   записями на stage-копии; зафиксировать ожидаемые 403 и допустимые переходы.
3. Выполнить миграцию `alembic upgrade head` и проверить backup/restore на копии.
4. Пройти живой shadow-run без изменения marketplace-карточек.
5. Получить owner approval для production и только затем менять сетевую публикацию.

M5 не запускает обход Auto.ru/Avito, не обходит CAPTCHA, не меняет кабинет
площадки, Google Sheets, Vehicle, Offer или цену.
