# Connect: common tasks. Run on the LXC from /opt/connect.
COMPOSE ?= docker compose
PY ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest

.PHONY: up down logs ps build restore-test turn-check test test-unit test-integration test-e2e test-phase0 \
        phase0-up phase0-down backup check-vendor check-brand audit

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

ps:
	$(COMPOSE) ps

build: check-vendor
	$(COMPOSE) build

check-vendor:
	./scripts/check-vendor.sh

backup:
	$(COMPOSE) exec -T backup sh /opt/backup/backup.sh once

# make restore-test FILE=/backups/connect-....dump : restores into a scratch DB and counts rows
restore-test:
	$(COMPOSE) exec -T backup sh /opt/backup/restore.sh $(FILE) --into connect_restore_test
	$(COMPOSE) exec -T backup psql -d connect_restore_test -c "select count(*) as alembic_rows from alembic_version"
	$(COMPOSE) exec -T backup dropdb connect_restore_test

turn-check:
	tests/integration/turn_check.sh $$(grep -E '^TURN_LISTEN_IP=' .env | cut -d= -f2-)

# Phase 0 spike stack and tests (TLS edge stand-in, stub app, local coturn).
phase0-up:
	tests/phase0/up.sh up -d --build

phase0-down:
	tests/phase0/up.sh down -v

test-phase0: check-vendor
	cd tests/e2e && ../../$(PYTEST) -q test_phase0_vdo.py

test: check-vendor test-phase0
