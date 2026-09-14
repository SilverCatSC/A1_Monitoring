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
- Local secret and evidence boundaries were tightened after the gate: `.env` is
  owner-readable only and `artifacts` (including the persistent Chrome profile)
  is owner-traversable only. Loopback readiness remained successful afterwards.
- The post-gate lifecycle deployment recovered one proven abandoned cycle through
  the lock-aware application route; `active_count` became zero. Details:
  [INTERRUPTED_CYCLE_RECOVERY_2026-09-14.md](INTERRUPTED_CYCLE_RECOVERY_2026-09-14.md).

## Post-repair runtime verification

After deployment of `25c55fe`, the same Mac/stage still reports app `0.14.0`
and Alembic `20260914_0011`. Loopback `/api/v1/health` and `/api/v1/ready` succeeded; the
previously failing `/api/v1/dashboard` and `/api/v1/dashboard/settings` routes
both returned HTTP `200`.

The repair aligns SQLAlchemy's `REVIEW_REQUIRED` member with the existing
PostgreSQL enum label `review_required`, while historical enum labels and data
remain untouched. This verification did not start a marketplace scan, change a
VPN route, publish data, or create role accounts.

## Post-auth-hardening runtime verification

После controlled deployment `fdd3ef4` на том же Mac/stage приложение осталось
на версии `0.14.0` и Alembic `20260914_0011`. На loopback успешно ответили
`/api/v1/health`, `/api/v1/ready`, `/api/v1/dashboard` и
`/api/v1/dashboard/settings`; `scripts/doctor.py --http --wait` вернул
`LOCAL_READY`, а `./scripts/run_monitoring_host_macos.sh --preflight` —
`preflight_succeeded` с `execution_model=readiness_only_no_cycle`.

Проверка подтверждает совместимость production-auth hardening со stage-профилем
`AUTH_ENABLED=false` на loopback. Она не создаёт и не проверяет реальные
учётные записи, Basic-auth, production network/publication, Chrome, VPN либо
marketplace cycle. Реальные роли и их приёмка остаются отдельными Gate 0/M7.

## Post-M6.9 MacBook-admission deployment verification

После commit `b56edd2` локальный Mac/stage image был пересобран и app-container
пересоздан без удаления volumes. Последующая проверка `docker-compose ps`
показала healthy `app` и `db`; `scripts/doctor.py --http --wait` завершился
`LOCAL_READY` с отключённым container autoscan.

Новый `./scripts/run_monitoring_host_macos.sh --preflight` завершился
`preflight_succeeded` / `HOST_RUNNER_PREFLIGHT_OK no_cycle_created=true` после
проверяемого inherited kernel-lock context. Он подтвердил только GUI/unlocked
host, services и loopback readiness: Chrome, source import, recovery, VPN
attestation и marketplace traffic не запускались.

Это runtime-совместимость нового fail-closed MacBook admission boundary со
stage-контуром, а не M7 acceptance. Валидная VPN attestation не создавалась;
VPSUS policy/IPv6 egress proof, реальные роли, controlled shadow-run, TCC и
LaunchAgent остаются отдельными owner gates.

### Lock-screen correction after the preflight evidence

Во время последующей безопасной проверки 2026-09-14 системный источник IOKit
одновременно вернул `IOConsoleLocked=false` и
`IOConsoleUsers.0.CGSSessionScreenIsLocked=true`. Это означает, что прежняя
проверка одного только `IOConsoleLocked` была недостаточна: она могла ошибочно
пропустить запуск на заблокированном экране. `run_monitoring_host_macos.sh` и
`register_monitoring_launchagent_macos.sh` скорректированы так, что оба
сигнала обязаны быть буквально `false`; true, отсутствие или ошибка чтения
дают отказ.

После исправления `./scripts/run_monitoring_host_macos.sh --preflight` на этой
же заблокированной сессии вернул
`HOST_RUNNER_REFUSED reason=screen_locked_or_state_unavailable` до Docker,
Chrome, VPN admission, cycle/recovery или сетевого обращения к площадкам. Это
проверяет только fail-closed lock boundary; успешный preflight и M7 acceptance
из этого не следуют и должны повторяться после ручной разблокировки владельцем.

## Boundary

This closes the backup/restore technical gate for the current Mac/stage only.
It does not configure real role accounts, create a publication allowlist,
change VPSUS routes, establish an accepted MacBook LaunchAgent runtime, or accept
M7. Windows/MSI runtime remains an unaccepted fallback rather than a current
release gate.
The latest v6 marketplace cycle remains a valid `partial` result pending link
reconciliation; see [AVITO_SELECTED_RADIUS_2026-09-14.md](AVITO_SELECTED_RADIUS_2026-09-14.md).
