install:
	pip install -e .

install-playwright:
	python -m playwright install chromium

init:
	python -m app.cli init

run-server:
	python -m app.cli serve

IMPORT_SOURCE = $(strip $(or $(SOURCE),$(SOURCE_CSV_PATH)))

run-import:
	@if [ -n "$(IMPORT_SOURCE)" ]; then \
		python -m app.cli import-source --path "$(IMPORT_SOURCE)"; \
	else \
		python -m app.cli import-source; \
	fi

run-scan:
	python -m app.cli scan

run-cycle:
	python -m app.cli run-cycle

scan: run-cycle
	@:

test:
	pip install -q -e .[dev]
	pytest -q

smoke:
	./scripts/smoke_stage.sh

DOCKER_COMPOSE ?= docker-compose

build:
	$(DOCKER_COMPOSE) build

up:
	$(DOCKER_COMPOSE) up -d

down:
	$(DOCKER_COMPOSE) down
