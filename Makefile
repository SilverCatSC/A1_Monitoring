PYTHON := .venv312/bin/python
.PHONY: setup setup-full start check test lint doctor scan scan-core watch retry-cycle backup restore-test smoke ui-check public-report bitrix-dry-run
setup:
	./scripts/setup.sh
setup-full:
	./scripts/install_all_macos.sh
start:
	./scripts/start_local.sh
check: lint test
test:
	$(PYTHON) -m pytest -q
lint:
	$(PYTHON) -m ruff check src tests scripts
doctor:
	$(PYTHON) scripts/doctor.py --http
smoke: doctor
ui-check:
	$(PYTHON) scripts/check_ui.py
scan:
	./scripts/run_full_monitoring_macos.sh
scan-core:
	./scripts/local_scan.sh --engines auto_ru,avito --pages 3 --pace cautious
watch:
	./scripts/local_scan.sh --watch --interval-minutes 360 --pace cautious
retry-cycle:
	@test -n "$(CYCLE_ID)" || (echo "Use: make retry-cycle CYCLE_ID=<partial-or-failed-cycle-id>" >&2; exit 2)
	$(PYTHON) -m app.cli retry-cycle $(CYCLE_ID)
backup:
	./scripts/backup_now.sh
restore-test:
	./scripts/restore_test.sh
public-report:
	@test -n "$(ALLOWLIST)" || (echo "Use: make public-report ALLOWLIST=/secure/path/publication_allowlist.json" >&2; exit 2)
	$(PYTHON) scripts/export_public_report.py --allowlist "$(ALLOWLIST)"
bitrix-dry-run:
	$(PYTHON) scripts/bitrix_publish.py --report-url https://silvercatsc.github.io/A1_Monitoring/
