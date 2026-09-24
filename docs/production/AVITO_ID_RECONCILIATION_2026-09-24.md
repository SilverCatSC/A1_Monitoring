# Avito placement ID: read-only reconciliation, 2026-09-24

## Scope and evidence

The owner reports that placement IDs are now displayed in Avito descriptions.
For the supplied listing `8176281881`, the owner pasted the first description
line as `ID: МBVC011220262508260009`. This is owner-provided card text, not
evidence from an approved monitoring cycle. The first character is Cyrillic
capital em (`U+041C`), while the agreed ID format requires ASCII Latin `M`
(`U+004D`). The intended-looking Latin string would be
`MBVC011220262508260009`, but the monitor must not silently substitute it.

The current card extractor records the labelled raw claim in
`card.placement_id_raw`. It fills `card.placement_id` only if the exact claim
passes the 22-character ASCII placement-ID contract. A mixed-script claim is
reported as `mixed_script_candidate` only when a narrow visual substitution
in the four-letter prefix points to exactly one valid ID in the two Avito
feeds and the active card has evidence. This is a review hint, not accepted
identity: the expected ID also remains `not_verified`. If the substituted ID
is absent or ambiguous in the feed, the claim remains `invalid_card_id`.
Numeric fields are never repaired. Neither finding can produce `link_current`
or `republication_candidate` or trigger an automatic URL change.

## Feed-to-card comparison

`src/app/service/marketplace_placement_reconciliation.py` now has a read-only
Avito comparison. It consumes both `avito-feed-new` and `avito-feed-used`,
including their actual first data row numbers, and accepts exact `Id` values
only. `AvitoId` remains the platform's numeric listing ID; it is not an A1
placement ID. Duplicate `Id` values across the two tabs block a link decision.
An active opened Avito card must have a valid direct-card evidence reference
and one valid labelled ID. Missing VIN, duplicate VIN, invalid stored URLs,
multiple public cards with one ID and unobserved IDs remain review states.

Avito feed rows are not assumed to be active solely because they exist in an
export. The reconciliation does not publish feeds, edit the marketing Sheet or
automatically replace URLs. It is not yet wired into the full dealer-catalogue
cycle, and there is no accepted live-cycle sample after the description change.

Price, year, direct-card status and search placement can still be checked
independently of the malformed ID. The malformed ID must not turn a closed old
link into a claim that the vehicle was sold or is absent from the platform.

## Immediate data correction

Marketing should replace only the first character of this listing's ID with
Latin `M`, then confirm that the outbound feed `Id` and rendered description
contain exactly the same 22-character string. Record the corrected card and
feed snapshot before using the ID to reconcile a stale link. The error was
reported on 24 September to the Bitrix24 group chat `А1 АВТО // Маркетинг`
with the listing URL and exact Unicode distinction. The message was visibly
present after sending; the listing correction itself is not yet verified.
