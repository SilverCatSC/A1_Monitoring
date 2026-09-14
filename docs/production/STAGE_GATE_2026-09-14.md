# Mac/stage gate record — 2026-09-14

## Scope

After commit `457c056`, the local Mac/stage application was redeployed with
Alembic head `20260914_0011`. The change separates the operator action
`review_required` from actual marketplace technical failures. No marketplace
scan, external export, publication or VPN-route change was made in this gate.

## Verified facts

- Deploy preflight and apply completed; app health and readiness succeeded on
  loopback.
- Running database revision equals source head: `20260914_0011`.
- A fresh checksum backup was created and its custom dump was read successfully.
- The dump restored into an isolated temporary database and was deleted after
  verification. The source and restored databases matched on `listings` (119),
  `source_import_snapshots` (52), `monitoring_cycles` (3), and
  `manager_feedback` (1).

## Boundary

This closes the backup/restore technical gate for the current Mac/stage only.
It does not configure real role accounts, create a publication allowlist,
change VPSUS routes, establish a Windows/MSI runtime result, or accept M7.
The latest v6 marketplace cycle remains a valid `partial` result pending link
reconciliation; see [AVITO_SELECTED_RADIUS_2026-09-14.md](AVITO_SELECTED_RADIUS_2026-09-14.md).
