# Placement ID contract — 2026-09-15

## Decision

The owner-defined ID is a **placement ID**, not a permanent physical-vehicle
ID. It is generated for publication on classified platforms and may be new for
a new sale even when the same car had appeared on A1 platforms before.

Therefore it is useful for correlating one publication through a feed, a
platform item ID and a URL. It must never by itself prove that two placements
refer to the same physical car or authorize automatic relinking after a new
sale. That requires a separate stable `a1_vehicle_id`, VIN or inventory ID.

## Exact format: 22 characters

```text
BBBB TTTT YYYY DDMMYY NNNN
```

| Segment | Length | Meaning | Rule |
| --- | ---: | --- | --- |
| `BBBB` | 4 | brand/model code | uppercase ASCII letters/digits from an approved dictionary |
| `TTTT` | 4 | vehicle type and subtype | numeric; first character is always `0` |
| `YYYY` | 4 | vehicle production year | independent from placement date |
| `DDMMYY` | 6 | placement date | valid calendar date |
| `NNNN` | 4 | placement sequence | `0001`–`9999`; scope must be managed by an issuance ledger |

Examples accepted by the contract:

| Placement ID | Vehicle year | Placement date |
| --- | ---: | --- |
| `MBVC011120260101260001` | 2026 | 01.01.2026 |
| `HOH6022120241506260206` | 2024 | 15.06.2026 |
| `RRSV022320222404260178` | 2022 | 24.04.2026 |

The second example proves an essential rule: `2024` is the model year, while
`150626` is the 2026 placement date. They must not be equated by validation.

## Required identities and journal

```text
a1_vehicle_id (stable physical/inventory identity)
  └─ placement_id (one sale/publication instance)
       └─ platform → platform_listing_id → URL → status → replaces_placement_id
```

When the same active sale is republished, retain the placement ID and record a
new platform URL/ID as a republication event. When the business defines a new
sale, issue a new placement ID and retain its link to `a1_vehicle_id` only when
the stable physical/inventory identity proves that relationship.

## Verified feed mapping — read-only source audit, 15 September 2026

The connected marketing workbook contains the actual outbound tabs, so the
field mapping is no longer a supposition:

| Feed tab | Customer placement field | Platform listing field | Evidence and result |
| --- | --- | --- | --- |
| `autoru-feed-all` | `unique_id` | not present in this outbound tab | `unique_id` contains valid 22-character placement IDs |
| `avito-feed-new` | `Id` | `AvitoId` | `Id` contains valid placement IDs; `AvitoId` is a separate numeric platform ID |
| `avito-feed-used` | `Id` | `AvitoId` | same schema, but at least one published row has a numeric `Id` equal to `AvitoId`, not a placement ID |
| `feed-dromru` | unknown | unknown | tab was empty in the inspected range; no contract may be inferred |

This proves the missing identity bridge for **Auto.ru and Avito**: the monitor
can receive the customer placement ID from the feed, retain the platform item
ID when provided, and associate the current direct URL after the item is
observed. It does **not** prove that two different placement IDs are the same
physical car.

## Direct-card declaration: current status, 22 September 2026

The placement ID is now present in the Auto.ru description block
`<div class="CardDescriptionHTML">` for the owner-reported current listings.
It is not yet present on Avito. This is source evidence, not yet a completed
monitoring-cycle observation: no controlled direct-card sample has been run
after the change.

The machine-readable declaration is:

```text
A1 ID: MBVC011220262508260027
```

`ID:` and `Идентификатор №` are accepted for migration compatibility. The
monitor can read the description of an opened direct card and accept an ID only
when it is both explicitly labelled and valid under this 22-character contract.
A bare number in the title, URL or description is deliberately not treated as
identity evidence.

For Auto.ru, the intended safe proof chain is now structurally available
without a dealer-cabinet API:

```text
marketing VIN → outbound-feed placement ID → labelled card description → platform URL
```

The first two links are supplied by the existing feeds; the last two need the
controlled card sample. In `autoru-feed-all!B2:C39`, 33 populated IDs were
read on 22 September: 29 `show`, 4 `hide`, and none violated the identifier
shape or the allowed action values. The ID remains unchanged when the same
placement is republished on a platform. A genuinely new sale/publication
receives a new ID, so the ID is not automatic proof that two different
placements are the same physical vehicle.

The marketing source table separately has VIN and platform URL columns, but no
placement-ID column. It is therefore a useful source of direct URLs and a
stable physical key, but cannot by itself join a feed row to a row in that
table without an explicit `placement_id` column or an audited VIN bridge.

## Feed integration gate

Before a feed is sent, run the identity-only gate over its export. The gate is
implemented in `src/app/service/feed_identity_audit.py` and can be called from
an export pipeline, or locally without publishing anything:

```bash
.venv312/bin/python scripts/audit_feed_identity.py --platform auto_ru --input /path/to/autoru.csv
.venv312/bin/python scripts/audit_feed_identity.py --platform avito --input /path/to/avito.csv --header-row 3
```

It refuses a missing/malformed 22-character customer ID, duplicate placement
ID within one feed, or an Avito row that has copied `AvitoId` into `Id`. A
blank `AvitoId` is allowed for a newly exported item. The gate does not send a
feed or update a link. Auto.ru's known export has its header on Sheet row 2;
for an Avito export, set `--header-row` to the actual header row if it is not 1.

`src/app/scraper/seller.py` contains a read-only extractor. It records
`card.placement_id` for a valid labelled declaration, but does not change a
registry URL or make a business decision. The next gate is one controlled
Auto.ru direct-card sample: an active `show` row must have exact equality
between `unique_id` and the ID in `CardDescriptionHTML`. Only then feed the
VIN-to-placement mapping into the local registry and allow an automatic URL
update for one exact expected ID on one active opened card. Zero or multiple
matches must remain `review_required`. Avito remains out of this gate until
the description declaration is actually implemented there.

`src/app/service/placement_identity.py` is a pure parser/formatter for this
contract only. It neither publishes data nor changes a platform link.

The local Auto.ru feed-to-card decision rules are documented in
[Auto.ru ID reconciliation](AUTORU_ID_RECONCILIATION_2026-09-24.md).
