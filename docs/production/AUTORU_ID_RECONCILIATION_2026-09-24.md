# Auto.ru ID reconciliation — local decision contract, 2026-09-24

## What this stage does

`src/app/service/autoru_placement_reconciliation.py` compares one feed snapshot
with a bounded set of seller cards already opened through the approved visible
browser runner. It produces reviewable findings without updating the marketing
Sheet, the local Listing URL, or an Auto.ru advertisement.

Inputs:

- `autoru-feed-all`: `unique_id`, `action` (`show` or `hide`) and `vin`;
- one direct-card inspection per opened candidate URL: `state`, extracted
  `card.placement_id`, evidence path and matching manifest name;
- an optional VIN-to-current-URL map from the marketing registry;
- whether the seller catalogue ended within the page limit.

The ID is the publication identity. It stays the same for an ordinary
republication with a new platform URL. A new sale may have a new ID for the
same vehicle, so the service never infers a vehicle match from model, price,
photo or year.

| Finding | Interpretation | Operator action |
| --- | --- | --- |
| `link_current` | The sole observed active card has the expected ID and the stored URL | No link change |
| `republication_candidate` | The sole observed active card has the expected ID at another URL | Review evidence and update the local link via the existing audited operation |
| `current_link_missing` / `current_link_invalid` | Card identified, source URL absent/invalid | Review the source row and link |
| `duplicate_public_id` / `duplicate_feed_id` / `ambiguous_feed_vin` | Identity is not unique | Resolve the data conflict before linking |
| `hidden_but_public` | Feed says `hide`, but an active card declares its ID | Check publication state; do not infer billing |
| `not_verified` | No accepted observation of the ID | Do not infer sale, absence or downtime |
| `card_evidence_missing` | Extracted ID lacks the required direct-card evidence | Repeat a controlled inspection |

The current Auto.ru feed snapshot read on 22 September had 29 `show` and four
`hide` IDs. That snapshot proves feed values only. The implementation now has
a bounded catalogue-card collector behind a disabled-by-default flag;
see [cycle integration](PLACEMENT_CYCLE_INTEGRATION_2026-09-24.md). It still
requires a controlled live sample before acceptance.
Avito now has a separate read-only comparison after the owner reported IDs in
its descriptions; see [Avito reconciliation](AVITO_ID_RECONCILIATION_2026-09-24.md).

## Release gate for automatic link updates

1. A controlled sample proves the ID in the opened Auto.ru description equals
   a `show` row's `unique_id` and has valid direct-card evidence.
2. The feed snapshot and marketing row are from a known freshness window, and
   the ID belongs to exactly one active vehicle.
3. Only one active card in the complete seller catalogue declares that ID.
4. The proposed URL belongs to Auto.ru and is not assigned to another active
   vehicle.
5. The audited `ListingLinkEvent`/`ListingLinkOverride` path is used. The old
   URL remains in history.

Until all five gates hold, `republication_candidate` is a review item, not a
request to replace a link automatically.
