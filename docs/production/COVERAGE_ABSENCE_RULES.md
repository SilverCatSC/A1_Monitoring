# Coverage and absence rules

A complete dealer catalogue is not proof that every card and publication identity
was verified. Dashboard inactivity and disappearance/republication alerts share
`service.coverage.absence_proven`.

Absence requires explicit complete enumeration AND complete card/ID verification,
no skipped/unverified cards and no blocked source. Missing verification flags in a
legacy report are uncertainty, not success. Newly collected reports retain the
catalogue candidate URLs; listed but uninspected URLs cannot disappear merely
because they are absent from observed_cards. Catalogue membership also prevents
an absence inference for that URL when coverage flags conflict.

A marketing row without a confirmed unique_id remains unbound. A missing old
link cannot establish that the vehicle is absent; active unbound adverts remain
separate records. VIN, price and model never bind those adverts. Proven direct
removal of a known publication can still establish inactivity during an otherwise
partial cycle. Confirmed positive active observations remain visible.

Saved-report readback adjusts unsupported historical alerts in memory, records
`coverage_reassessment`, and regenerates the marketing summary using its frozen
decision provenance. Source reports, evidence files, timestamps and bundles are
not rewritten; no cycle is marked complete. Supported removals remain intact.

The always-visible status panel shows saved/resumed cycle status and separate
per-platform enumeration and card/ID completeness. It is outside every
collapsible section on phone, tablet and laptop. An unselected platform says
“не проверялось”, rather than implying a failed check.

## Verification (28 October 2026)

- Production-service regressions: zero inspection limit, preflight block, and a
  block occurring after the first inspection; output replayed through dashboard
  and alert generation.
- Projection regressions: missing completeness proof, listed but uninspected
  URLs, missing unique_id with an active new URL, and evidenced direct removal.
- Readback regression: stale confirmed absence is downgraded without changing
  the original file.
- Saved cycles replayed: 658eec46-87a1-4eec-ac6c-4e84f1960c34 and
  61ba812c-8fe9-4bec-9640-7683b405395f. Latest report: 18 supported inactive
  publications; two previously inactive records remain uncertain; five Auto.ru
  disappearance alerts become unverified. Avito results remain supported.
- Latest saved evidence/search integrity replay passed. Hashes unchanged.
- Status-panel checks: 320, 390, 640, 768, 1024 and 1366 CSS pixels; panel visible
  above the fold and no page overflow. No live marketplace scan.

Evidence and test logs: `artifacts/coverage_fix_20261028/`.
