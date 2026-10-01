# Connect: common tasks. Run on the LXC from /opt/connect.
COMPOSE ?= docker compose
PY ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest

.PHONY: up down logs ps build test test-unit test-integration test-e2e test-phase0 \
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

# Phase 0 spike stack and tests (TLS edge stand-in, stub app, local coturn).
phase0-up:
	tests/phase0/up.sh up -d --build

phase0-down:
	tests/phase0/up.sh down -v

test-phase0: check-vendor
	cd tests/e2e && ../../$(PYTEST) -q test_phase0_vdo.py

test: check-vendor test-phase0
