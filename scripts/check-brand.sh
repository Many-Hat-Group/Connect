#!/bin/sh
# White-label check: brand strings may only appear in app/defaults.yaml and
# .env.example. Scans shipped code, templates, static files and config.
set -eu
cd "$(dirname "$0")/.."
PATTERN='many[ ._-]?hat|\bMHS\b|mhits'
hits=$(grep -rniE "$PATTERN" \
  app router coturn signal certbot backup scripts install.sh docker-compose.yml Dockerfile Makefile \
  --exclude-dir=__pycache__ --exclude=defaults.yaml --exclude=check-brand.sh 2>/dev/null || true)
if [ -n "$hits" ]; then
  echo "check-brand: brand-specific strings found outside app/defaults.yaml and .env.example:"
  echo "$hits"
  exit 1
fi
echo "check-brand: OK"
