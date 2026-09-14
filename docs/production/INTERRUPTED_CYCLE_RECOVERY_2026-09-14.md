# Interrupted-cycle recovery record — 2026-09-14

## Incident

Cycle `02f51801-30c3-451e-bb75-90a4c78685a5` had a sealed 34-record roster but
remained `running` after its local runner was interrupted. It had no scan runs,
no terminal timestamp and no error. This was a lifecycle-recording defect, not
a marketplace result; treating it as active would make operations status lie.

## Corrective change

Commit `084b520` makes `MonitoringCycleService` terminalize a registered cycle
on `KeyboardInterrupt`; the local runner maps `SIGTERM` to the same path. It
also adds an admin/operator recovery route and local CLI that acquire the
complete-cycle lock before touching any open row. Recovery records an actor,
timestamp and `interrupted_runner_recovery`, preserves the immutable manifest,
and does not import data, open Chrome, call a marketplace or modify observations.

## Stage execution

After the stage deployment, `POST /api/v1/cycles/recover-open` recovered exactly
the affected cycle as `failed`. Its manifest path and roster hash remained
unchanged. A second call returned zero recovered rows, proving the action is
idempotent. `/api/v1/status/operations` then reported `active_count: 0` and the
cycle became eligible only for an explicit ordinary retry.

## Boundary

The recovery resolves the phantom runtime state, not the business queue. The
latest v6 cycle remains `partial`: Auto.ru link reconciliation and direct-card
review are open, while Avito search completed successfully. See
[AVITO_SELECTED_RADIUS_2026-09-14.md](AVITO_SELECTED_RADIUS_2026-09-14.md).
