# Controlled deployment record — 2026-09-14

## Scope

Подтверждён и выполнен controlled deploy текущего main на локальном
Mac/stage. Volumes не удалялись; marketplace scan, внешний export и изменение
VPN-маршрутов не выполнялись.

## Evidence

- Предварительный state: app 0.9.0, Alembic 20260909_0006.
- Target source revision: Alembic 20260914_0010.
- Deploy завершился: app 0.14.0, Alembic 20260914_0010.
- Health и ready ответили успешно на loopback.
- Read-only stage smoke завершился LOCAL_READY.
- Новый SHA-256 backup прошёл isolated restore-test; revision 20260914_0010,
  контрольные количества: listings 106, source_import_snapshots 48,
  monitoring_cycles 0, manager_feedback 1.
- Historical backup старой схемы также прошёл isolated restore-test:
  revision 20260909_0006; отсутствующая в source schema monitoring_cycles
  обозначена как skipped, а не выдана за ошибку.

## Remaining acceptance gates

M7 не закрыт. Нужны отдельно подтверждённые VPN route tests, MacBook shadow-run
через видимый Chrome, owner decision по ролям, ограничениям и rollback, а при
включении графика — отдельный per-user LaunchAgent gate. Windows/MSI acceptance
сохранён как резервный handoff и больше не является обязательным MacBook gate.
