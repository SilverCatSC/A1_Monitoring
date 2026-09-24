# Avito placement ID: read-only reconciliation, 2026-09-24

## Scope and evidence

The owner reports that placement IDs are now displayed in Avito descriptions.
For the supplied listing `8176281881`, the owner pasted the first description
line as `ID: МBVC011220262508260009`. This is owner-provided card text, not
evidence from an approved monitoring cycle. The first character is Cyrillic
capital em (`U+041C`), while the agreed ID format requires ASCII Latin `M`
(`U+004D`). The intended-looking Latin string would be
`MBVC011220262508260009`, but the monitor must not silently substitute it.

A read-only check of the connected marketing workbook on 2026-09-24 found
`avito-feed-new!A9:C9`: `Id=MBVC011220262508260009` (Latin `M`) and
`AvitoId=8176281881`. The numeric AvitoId equals the listing number in the
owner-supplied URL. This confirms a feed-to-URL mapping in the current source
table; it does **not** confirm that the public card is currently active or that
the description is still unchanged.

The current card extractor records the labelled raw claim in
`card.placement_id_raw`. It fills `card.placement_id` only if the exact claim
passes the 22-character ASCII placement-ID contract. A mixed-script claim can
be used for operational reconciliation when a narrow visual substitution in
the four-character brand/model prefix points to exactly one valid ID across
both Avito feeds and the active card has evidence. The result carries
`id_match_basis=visual_alias` and a separate `mixed_script_id` warning, while
still returning `link_current` or `republication_candidate` as appropriate.
This lets monitoring continue without waiting for marketing to correct the
copy. The raw claim remains in evidence. The same narrow rule applies to a
mixed-script feed `Id`: it produces a separate `mixed_script_feed_id` warning
but can still match a card operationally. Duplicates are counted *after*
normalization across both Avito feed tabs and block the match. The strict
pre-publication feed audit still rejects the bad source value. If a card's
substituted ID is absent or ambiguous in the feed, the claim remains
`invalid_card_id`. Numeric fields are never repaired. None of these read-only
results automatically changes a URL.

## Feed-to-card comparison

`src/app/service/marketplace_placement_reconciliation.py` now has a read-only
Avito comparison. It consumes both `avito-feed-new` and `avito-feed-used`,
including their actual first data row numbers. `AvitoId` is the platform's
numeric listing ID, never a substitute for the A1 placement ID in the `Id`
column. When the URL's listing number equals one *unique* AvitoId across both
feed tabs, it provides additional feed-to-URL corroboration. Because the feed
can lag after republication, AvitoId alone produces only
`platform_id_candidate`, not a confirmed identity or new URL. A visual-alias
ID and matching AvitoId provide two agreeing anchors. If the card ID and
AvitoId point to different feed rows,
the result is `identity_conflict` and neither row receives the card. Duplicate
`Id` or duplicate AvitoId across the tabs also blocks a link decision. A
republished listing can still be found through its exact or narrowly normalized
description ID when the feed's old AvitoId has not yet been updated. Every
active opened card requires valid direct-card evidence; the feed row alone is
not proof of an active listing. Missing VIN, duplicate VIN, invalid stored
URLs, multiple public cards with one ID and unobserved IDs remain review states.

Avito feed rows are not assumed to be active solely because they exist in an
export. The reconciliation does not publish feeds, edit the marketing Sheet or
automatically replace URLs. It is not yet wired into the full dealer-catalogue
cycle, and there is no accepted live-cycle sample after the description change.

Price, year, direct-card status and search placement can still be checked
independently of the malformed ID. The malformed ID must not turn a closed old
link into a claim that the vehicle was sold or is absent from the platform.

## Immediate data correction

Marketing should eventually replace only the first character of this listing's
ID with Latin `M` and confirm that the feed `Id` and rendered description
contain the same 22-character string. This correction is a data-quality task,
not a prerequisite for an evidenced operational match under the rules above.
The error was reported on 24 September to the Bitrix24 group chat
`А1 АВТО // Маркетинг` with the listing URL and exact Unicode distinction. The
message was visibly present after sending; the listing correction itself is
not yet verified.
