# A1 Monitoring: цель, статус и следующие решения — 2026-09-14

## Конечная цель

Это не «скрипт, который ищет объявления». Целевой результат — управляемый
production-контур на основном MacBook, который осторожно проверяет размещение
активных автомобилей A1 на Auto.ru и Avito, сохраняет evidence и разделяет
четыре разных бизнес-состояния:

1. карточка найдена;
2. карточка не найдена, но вывод ещё не доказан;
3. ссылка/объявление требует сверки или перевыкладки;
4. площадка технически недоступна.

Контур не меняет объявления на площадках, не обходит CAPTCHA и не называет
старую закрытую карточку продажей автомобиля. Его задача — дать оператору
доказуемую очередь действий, а не автоматизировать рискованные выводы.

## Статус на текущий момент

Проценты ниже — управленческая оценка готовности, а не результат одного
технического теста. Они показывают, где находится фактический риск.

| Контур | Оценка | Что доказано | Что остаётся |
| --- | ---: | --- | --- |
| Инженерная основа | 90% | MacBook-only entrypoint, lock-screen gate, private VPN admission, backup/restore, тесты и документация | Поддерживать regression-gates при следующих изменениях |
| Runtime MacBook + VPSUS | 80% | VPSUS connected, Auto.ru/Avito direct-mode exceptions видимы, dual-stack reachability, host preflight и один controlled cycle прошли | Финальный M7 owner sign-off; public egress attribution не заявляется доказанной |
| Поиск и evidence | 75% | Controlled cycle прошёл Auto.ru и Avito без технических ошибок, evidence сохранён, Avito selected-radius соблюдён | Ручная контрольная выборка M7 и решение по расширенному run |
| Качество ссылок/перевыкладок | 65% | 46 из 68 source-link связей подтверждены в свежем каталоге продавца; подтверждён feed mapping `VIN → unique_id`/`Id → platform item ID`; обе площадки, по сообщению владельца, выводят ID в описании, read-only сверка реализована | 14 отсутствующих валидных ссылок и 8 закрытых/ambiguous связей требуют републикационной сверки; feed-to-registry import и controlled card samples ещё не включены; пример Avito содержит смешение кириллицы и латиницы в ID |
| Формальная production-приёмка M7 | 35% | Технические gates и один корректный `partial` доказаны | Контрольная выборка, реальные роли, owner sign-off и, только если нужен график, LaunchAgent acceptance |

**Интегральная готовность: около 68%.** Это означает: платформа уже работает
как безопасный наблюдатель, но пока не должна называться полностью принятой
production-системой. Главный остаточный риск — качество привязки «машина ↔
актуальное объявление», а не очередной рефакторинг.

## Что означают текущие `missing_link`, `removed` и `review_required`

| Статус | Что он означает | Чего он не означает |
| --- | --- | --- |
| `missing_link` | В исходном реестре для этой площадки нет валидной прямой ссылки | Не утверждает, что автомобиля нет или он продан |
| `removed` | По старому URL площадка показала «снято», «закрыто» или «продано» | Не подтверждает продажу автомобиля компании |
| `review_required` | Старый ID не найден в свежей части каталога или связь неоднозначна | Не является технической ошибкой и не должен автоматически менять ссылку |

Итог controlled cycle `527c4ad1-20fd-4967-a814-31ce75fea124`: zero technical
errors; 14 `missing_link`; восемь других non-verified записей корректно
остались `removed` или `review_required`. Детали без персональных/рыночных
данных: [M7 controlled-cycle evidence](M7_CONTROLLED_CYCLE_2026-09-14.md).

## Перевыкладка: как должно работать

Нужный процесс действительно такой: старая карточка закрыта, в каталоге дилера
нашлась новая карточка той же машины, ссылка у активного автомобиля заменяется
и остаётся audit trail old URL → new URL. Но безопасно делать это автоматически
можно только при **однозначном устойчивом идентификаторе**.

Сейчас в реестре есть VIN у 31 из 34 активных автомобилей, но в свежих
карточках каталога дилера нет структурированного VIN и ни один известный VIN
не найден в доступном тексте карточек. Поэтому совпадение по марке/модели,
цене или году было бы небезопасной догадкой: одинаковых автомобилей может быть
несколько. Нынешняя система уже показывает candidate и позволяет оператору
подтвердить замену; она сознательно не перепривязывает ссылку сама.

### M7.1 — deterministic republication mapping

Связь не обязана быть видна покупателю в публичной карточке. Она создаётся в
момент публикации и хранится в нашем журнале размещений. Нужен один
достоверный канал, предпочтительно export/API из кабинета дилера, с двумя
разными связями:

```text
стабильный ID автомобиля (или VIN) → placement ID
placement ID → площадка → platform item ID → текущий URL
```

После его появления правило будет строгим:

1. новый URL допустим только для Auto.ru/Avito и только из свежего каталога;
2. ID/VIN сопоставляется ровно с одним активным автомобилем;
3. URL не связан с другим активным автомобилем;
4. система создаёт `ListingLinkEvent` и pinned `ListingLinkOverride` с причиной
   `verified_republication`; старый URL сохраняется в audit trail;
5. нулевое или множественное совпадение остаётся `review_required`, без
   автоматической замены.

Read-only audit of the real outbound workbook (15 September) has now confirmed
the second relation for the two active platforms: Auto.ru `unique_id` and
Avito `Id` contain the 22-character **placement ID**; Avito `AvitoId` is the
separate platform listing ID. This is a material advance, but it corrects one
earlier assumption: `unique_id`/`Id` must not be treated as a stable vehicle
ID, because a new sale deliberately gets a new placement ID.

The remaining ownership boundary is therefore clear. The existing feed rows
already provide an audited `VIN → placement ID` bridge, so a visible
`placement_id` column in the main marketing table is recommended for operator
transparency rather than a technical precondition. Keep a read-only publication
journal containing `a1_vehicle_id`/VIN, placement ID, platform, platform item
ID and URL. The new pre-publication gate rejects malformed IDs, duplicates in a
feed and an Avito `Id` accidentally replaced by `AvitoId`. A direct-card
reader is ready for both platforms, but it has no production evidentiary role
until controlled direct-card samples verify the exact rendered IDs. The owner-
provided Avito sample has a mixed-script ID and needs correction. Neither component
publishes anything or guesses a link. Drom has no accepted identity contract
yet because its inspected feed tab contained no values.

Выбор источника — owner decision: без него нельзя безопасно «угадать» связь по
внешнему виду карточки.

## Понятный путь к 100%

1. **Провести Auto.ru direct-card sample**: ID активной строки `show` из
   `unique_id` должен совпасть с ID в описании. После этого включить read-only
   import связи `VIN → placement_id` из фидов и журнал публикаций; сопоставлять
   со считанным из карточки ID. Для Avito исправить смешанный алфавит в
   предоставленном примере и выполнить такой же controlled sample. Колонка
   `placement_id` в основной таблице
   рекомендуется для прозрачности, но не блокирует этот этап.
2. **Сверить текущую очередь**: 14 записей без ссылки и 8 снятых/ambiguous
   карточек; оператор подтверждает только фактические новые URL.
3. **Согласовать Drom contract** только после появления непустого реального
   фида или документации кабинета; не переносить схему Avito на него по аналогии.
4. **Повторить один owner-approved shadow-cycle** после сверки. Результат
   `completed` должен быть следствием чистых данных, а не ослабления правил.
5. **Закрыть owner gates M7**: контрольная выборка, реальные роли, сроки
   хранения evidence и rollback sign-off.
6. **Решить, нужен ли график.** Если да — отдельно принять LaunchAgent в
   активной GUI-сессии MacBook. Если нет — оставить только ручной controlled
   runner; это допустимый production operating model.

## Связанные записи

- [M7 acceptance runbook](ACCEPTANCE_M7.md)
- [M7 controlled-cycle evidence](M7_CONTROLLED_CYCLE_2026-09-14.md)
- [VPSUS gate](VPN_GATE_2026-09-14.md)
- [Avito selected-radius contract](AVITO_SELECTED_RADIUS_2026-09-14.md)
