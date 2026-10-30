# Дашборд мониторинга автомобилей

Цель — показать состояние размещения, позиции и обнаруженные несоответствия.
Маркетинг выясняет причины и исправляет публикации/источники. Продажи подтверждают
характеристики и условия продажи. Отчёт не ждёт решения всех бизнес-проблем.

С 27 октября раздел «Что проверить маркетингу» формируется автоматически после
цикла и доступен отдельным текстом. Сохранённые подтверждения и исключения
учитываются в действиях, ярлыках и пояснениях карточек. Правила, управление
реестром и воспроизведение на архиве: [ANALYTICAL_SUMMARY](ANALYTICAL_SUMMARY.md).

Основной экран: счётчики, поиск по модели/VIN/unique_id/ответственному, фильтр
проблем и карточки автомобилей. Данные маркетинга подписаны отдельно от значений
каждой площадки. Изображение — фото из сохранённого фида либо снимок объявления;
источник подписан, отсутствие фото не скрывает автомобиль. Количество владельцев
не означает имя юридического собственника; если данных нет, это показано явно.

- Объявления объединяются только по подтверждённому unique_id. VIN используется
  исключительно для связи строк книги. Уже проверенная связь из content review
  может использоваться повторно. Объявления без ID остаются отдельными записями:
  количество плиток не является количеством уникальных физических автомобилей.
- «Неактивно» относится к ожидаемой публикации: в актуальной строке есть ссылка,
  полный каталог прочитан, публикации по известному ID/ссылке в нём нет.
  Это не продажа и не диагноз причины; перевыкладка без ID не исключена.
- Неполный каталог даёт «Не проверено». Активная старая ссылка без подтверждённой
  связи с маркетингом предотвращает ложное отсутствие, но не назначает ID.
- Активная карточка вне первых пяти страниц применимого завершённого поиска —
  «5+». Недоказанная применимость фильтра или отсутствие фильтра — неизвестная позиция.
- Нет ссылки в маркетинге — размещение не ожидается по этой таблице; фактически
  найденные активные публикации всё равно показываются. Полностью неопубликованные
  строки без ссылок имеют нейтральный статус.
- Различия текста фида и описания, способ подтверждения ID не превращаются сами
  по себе в ошибки. Исходные замечания, даты и ссылки доступны по раскрытию.

`marketing_vehicle_snapshot` сохраняется при очередной штатной сверке с книгой,
без дополнительного обращения к Google/площадкам. Для старого цикла используется
отдельное дополнение с контрольной суммой, только из его проверенного снимка книги.
Исходные снимки и статус partial не переписываются. Перестройка представления не
является новым прогоном и не подтверждает текущую доступность публикаций.

## Responsive interface review — 2026-10-28

The previous layout put a large recovery panel and expanded technical findings
ahead of the vehicle grid. Mobile navigation required sideways scrolling; some
expanded evidence rows and comparison tables could exceed a narrow viewport.

The report uses a light workspace with floating desktop navigation, segmented view
switches and functional overview cells. The owner's CreativeJuice and
[Mira reference](https://www.awwwards.com/inspiration/mira-start-from-within-miralife-app)
inform the hierarchy and mobile card composition, rather than the color palette.
CSS and SVG are original; existing A1 report photos/data are preserved. Inter is
self-hosted from its official repository under the included SIL Open Font License;
no remote font service or copied reference imagery is used.

- Overview counts come first; vehicles and marketing actions have separate
  keyboard-accessible tabs. Existing `#marketing-summary` links select the
  marketing view. Without JavaScript, both sections remain readable.
- Desktop has three vehicle columns, tablet two, and narrow phones one.
  Tablet/phone navigation uses a native expandable menu rather than an
  off-screen strip. Primary touch controls are at least 44px high; text inputs
  use 16px type. VINs, IDs and long source values wrap.
- Technical evidence remains expandable. Wide comparison tables scroll inside
  a labeled, keyboard-focusable region; status rows wrap instead of widening
  the page. Missing images have a visible fallback.
- The inactive-publication counter counts platform publications, whereas the
  vehicle filter counts vehicle records; their counts can legitimately differ.
- Static CSS/JS links carry a content-based deployment revision so cached assets
  do not leave old navigation or scripts on the new page.

Verification evidence is saved in `artifacts/ui_responsive_20261028/`: viewport
measurements, screenshots, interaction checks and the focused test log. This is
interface validation against the saved report, not a new marketplace scan or
resolution of the Avito catalogue discrepancy.

The vehicle list initially reveals 12 records. Show more and Show all expose the
remaining records; search and filters always evaluate the complete saved list.
Overview actions open the relevant vehicle filter or identity issues. Transitions
are brief and respect reduced-motion preferences; scrolling is never captured.
Structural light-theme verification: `artifacts/ui_structure_light_20261028/`.

Phone typography was enlarged after owner review: vehicle values 16px, field labels
14px, card titles 20px, prices 28px and marketing text 15px. Phone controls are
at least 48px high. Verified at 320, 390 and 430 CSS pixels, including expanded
marketing findings; no horizontal page overflow. Desktop type is unchanged.
Evidence: `artifacts/ui_mobile_type_20261028/`.

A subsequent criteria-based responsive audit and the owner's compact-card request
are recorded in `UI_REVIEW_20261028.md`. Publication prices stay visible by source;
marketing price, specifications and discrepancies now expand from Details.

Coverage and absence corrections, saved-report readback and always-visible status
are documented in `COVERAGE_ABSENCE_RULES.md`.
