# M3: контракт доказательств площадок

Статус: реализовано в коде `0.12.0`; это offline-gate, не подтверждение живой
доступности Auto.ru или Avito. Контракт применяется к новым browser-collectors.
Старые screenshots остаются доступными как исторические файлы, но без manifest не
считаются M3-доказательством целостности.

## Что считается доказательством

Каждый browser-collector сохраняет PNG viewport и рядом файл `<png>.json` со
схемой `evidence.v1`. Manifest содержит:

- площадку, назначение (`search_page`, `search_listing_card`, `direct_card`) и
  номер страницы;
- имя PNG, размер байтов, SHA-256 и UTC-время сохранения;
- SHA-256 requested/final URL, а для точной карточки — canonical external key;
- HTTP status, если браузер его получил.

В manifest не дублируются полный URL, VIN, текст объявления, cookies или HTML.
Проверка manifest повторно вычисляет SHA-256 изображения и отклоняет изменение,
символическую ссылку, выход за evidence directory или неполную схему.

## Fail-closed правила

| Событие | Результат collector | Разрешённый бизнес-вывод |
| --- | --- | --- |
| CAPTCHA / access denied / 401/403/429 | `blocked` и сохранённый снимок | Только technical state; не «объявление отсутствует» |
| Неизвестная вёрстка без карточек | `unrecognized` и сохранённый снимок | Нет вывода о пустой выдаче |
| Снимок страницы не сохранился | `evidence_missing`, traversal incomplete | Нет `found`/absence по этой выдаче |
| Целевая карточка найдена, но её точный screenshot не сохранился | `evidence_missing`, traversal incomplete | Нет `found` для целевого Offer |
| Прямая карточка открылась, но screenshot не сохранился | `unknown/evidence_missing` | Нет active/removed/sold вывода |
| Явная пустая выдача с screenshot | `empty` | Только граница обхода, не факт продажи |

Таким образом PNG страницы не подменяется «успешным парсингом», а смена вёрстки не
превращается в нулевой результат. Screenshot exact target card требуется, потому
что viewport всей выдачи может не содержать виртуализированную карточку.

## Контракты источников и fixtures

| Источник | Поддерживаемый контур M3 | Проверяемые fixtures |
| --- | --- | --- |
| Auto.ru | выдача, pagination, exact-card screenshot, direct-card status | CAPTCHA, неизвестная layout |
| Avito | выдача, pagination, exact-card screenshot, direct-card status/VAT | access denied, явная пустая выдача |
| Drom / новый сайт | Не подключаются в M3 | Нужны отдельный source contract и владелец приёмки |

Fixtures лежат в `tests/fixtures/marketplaces/`; unit-тесты фиксируют, что CAPTCHA
и blocked не распознаются как empty, а неизвестная Auto.ru layout не завершает
обход как пустая. Playwright regression дополнительно проверяет, что отсутствие
exact-card screenshot делает выдачу неполной.

## Доступ и срок хранения

Новые API читают проверенный manifest, не строя путь из запроса:

```text
GET /api/v1/observations/{observation_id}/evidence/{page_number}/manifest
GET /api/v1/reconciliations/{reconciliation_id}/evidence/manifest
```

Удаление устаревшего PNG в `cleanup_evidence` удаляет и его companion manifest.
Обычная резервная копия сохраняет оба файла. Нельзя «починить» evidence ручной
заменой PNG: его hash будет отличаться, а системе потребуется новый контрольный
сбор или документированное решение оператора.

## За пределами M3

M3 не делает живых запросов к площадкам, не обходит CAPTCHA, не гарантирует
совместимость будущей вёрстки и не подтверждает полноту результатов за пределами
проверенного окна. Это проверяют shadow-run и owner acceptance M7.
