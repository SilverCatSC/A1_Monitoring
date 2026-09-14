# Live probes marketplaces — 2026-09-14

## Scope

Проверка выполнена через изолированный видимый Chrome profile команды
local_scan в режиме probe. Этот режим не создаёт observations, monitoring cycle
или изменение source registry. Каждая площадка получила только один
осторожный запрос первой страницы.

## Result

- Avito: HTTP 200, одна страница, 13 распознанных карточек, результат
  LOCAL_PROBE_OK.
- Auto.ru: первая проверка дала HTTP 200, но корректно остановилась как parser
  uncertainty. Evidence показал, что Auto.ru сначала отдаёт model-summary root
  без карточек, а затем отдельный root с фактическими offers.
- Исправление 054c1fb выбирает approved result root с наибольшим числом
  candidate cards, сохраняя порядок при равенстве.
- Повторный Auto.ru probe: HTTP 200, одна страница, 14 распознанных карточек,
  результат LOCAL_PROBE_OK.

## Deployment evidence

После исправления образ application пересобран и перезапущен. Stage smoke,
checksum backup, isolated restore-test и deploy preflight прошли; app version
осталась 0.14.0, Alembic revision 20260914_0010.

## Boundary

Probe подтверждает, что текущие адаптеры могут прочитать по одной странице через
проверенный Mac routing. Он не является полным monitoring cycle, не проверяет
весь roster, не подтверждает бизнес-результат absence и не закрывает M7.
Полный shadow-run требует утверждённой контрольной выборки и последующей ручной
сверки evidence.
