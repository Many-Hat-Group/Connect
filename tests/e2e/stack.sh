#!/bin/sh
# E2E stack control: tests/e2e/stack.sh up -d | down -v | logs app | exec ...
set -eu
cd "$(dirname "$0")/../.."
exec docker compose -p connect-e2e --env-file tests/e2e/env.e2e -f docker-compose.yml -f tests/e2e/compose.e2e.yml "$@"
