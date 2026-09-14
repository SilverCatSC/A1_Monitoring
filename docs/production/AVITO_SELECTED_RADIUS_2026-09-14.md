# Avito: selected-radius contract — 2026-09-14

## Owner correction

For Avito, a Moscow path and zero radius were insufficient. The approved
search contract now has all four parts:

1. URL path starts with `/moskva/`.
2. `radius=0`.
3. `searchRadius=0`.
4. `localPriority=1` — the visible Avito switch is on and is labelled
   `Сначала в выбранном радиусе`.

This replaces the obsolete `localPriority=0` configuration. The canonical
catalogue version is `a1-monitoring-rules-2026-09-14-v6`.

## Verification

- In the isolated local Chrome worker, the Hongqi HQ9 page with
  `localPriority=0` exposed the switch with `aria-checked=false`.
- The same URL with `localPriority=1` returned HTTP 200 and exposed
  `aria-checked=true` / checked input.
- The controlled stage deployment of commit `14f4d6a` completed with
  `/api/v1/health` and `/api/v1/ready` successful.
- `POST /api/v1/filters/catalog/sync` updated exactly six Avito canonical
  filters, retained 38 assignments, and created no filters.
- A no-write Hongqi HQ9 adapter probe completed with 13 Moscow hits.

## Interpretation and boundary

`localPriority=1` selects the approved Avito UI mode. Avito may still offer
later pagination for other cities; that is marketplace behaviour, not proof
of a Moscow listing. The adapter retains a second guard: only listing URLs
whose path starts with `/moskva/` become monitoring hits. Other visible cards
are classified as a valid page, never as a Moscow match or absence.

The interrupted cycle started before this correction is not acceptance
evidence. It was stopped before marketplace-filter scanning and will not be
used to conclude availability, absence, or M7 acceptance. A fresh controlled
cycle must use the v6 catalogue.
