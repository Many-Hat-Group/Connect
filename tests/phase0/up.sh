#!/bin/sh
# Bring up the Phase 0 spike stack.
set -eu
cd "$(dirname "$0")/../.."
docker compose -p connect-phase0 --env-file tests/phase0/env.phase0 -f docker-compose.yml -f tests/phase0/docker-compose.phase0.yml "$@"
