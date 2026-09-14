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

## Stabilisation after the first partial cycle

- Auto.ru model filters now request `output_type=list`; the active Moscow
  radius counter is used instead of an unrelated offer-count phrase. A Hongqi
  HQ9 runtime probe completed on page 1 with 14/14 cards.
- Avito waits once for late-rendered cards and falls back from an empty
  `catalog-serp` marker to visible sibling cards. A Hongqi HQ9 runtime probe
  recognised page 2 as valid (46 visible non-Moscow cards, zero in-scope hits),
  rather than a parser error.
- The subsequent owner correction changes the Avito contract to
  `localPriority=1`; its separate evidence and limitation are recorded in
  [AVITO_SELECTED_RADIUS_2026-09-14.md](AVITO_SELECTED_RADIUS_2026-09-14.md).
  The earlier partial run and a cycle interrupted before filter scanning are
  historical diagnostics only, not acceptance evidence.

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
