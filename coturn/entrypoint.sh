#!/bin/sh
# Renders turnserver.conf.tmpl from the environment and starts coturn.
set -eu

: "${TURN_LISTEN_IP:?}" "${TURN_RELAY_IP:?}" "${TURN_EXTERNAL_IP:?}" "${TURN_REALM:?}" "${TURN_SHARED_SECRET:?}"
: "${TURN_MIN_PORT:=49160}" "${TURN_MAX_PORT:=49200}"
: "${TURN_USER_QUOTA:=12}" "${TURN_TOTAL_QUOTA:=60}" "${TURN_MAX_BPS:=0}"
: "${TURN_CERT_FILE:=/certs/fullchain.pem}" "${TURN_KEY_FILE:=/certs/privkey.pem}"
: "${TURN_EXTRA_DENIED_RANGES:=}"

TURN_EXTRA_DENIED_LINES=""
if [ -n "$TURN_EXTRA_DENIED_RANGES" ]; then
  TURN_EXTRA_DENIED_LINES=$(printf '%s' "$TURN_EXTRA_DENIED_RANGES" | tr ',' '\n' | sed '/^ *$/d; s/^ *//; s/ *$//; s/^/denied-peer-ip=/')
fi
export TURN_LISTEN_IP TURN_RELAY_IP TURN_EXTERNAL_IP TURN_REALM TURN_SHARED_SECRET \
  TURN_MIN_PORT TURN_MAX_PORT TURN_USER_QUOTA TURN_TOTAL_QUOTA TURN_MAX_BPS \
  TURN_CERT_FILE TURN_KEY_FILE TURN_EXTRA_DENIED_LINES

out=/tmp/turnserver.conf
# Minimal envsubst: replace ${VAR} for the exported names above only.
cp /etc/coturn-tmpl/turnserver.conf.tmpl "$out"
for v in TURN_LISTEN_IP TURN_RELAY_IP TURN_EXTERNAL_IP TURN_REALM TURN_SHARED_SECRET \
  TURN_MIN_PORT TURN_MAX_PORT TURN_USER_QUOTA TURN_TOTAL_QUOTA TURN_MAX_BPS \
  TURN_CERT_FILE TURN_KEY_FILE; do
  eval "val=\${$v}"
  esc=$(printf '%s' "$val" | sed 's/[\/&|]/\\&/g')
  sed -i "s|\${$v}|$esc|g" "$out"
done
# Multi-line value: write via awk.
awk -v lines="$TURN_EXTRA_DENIED_LINES" '{ if ($0 == "${TURN_EXTRA_DENIED_LINES}") print lines; else print }' "$out" > "$out.new" && mv "$out.new" "$out"

# Test-only overlay (never set in production): extra config lines appended.
if [ -n "${TURN_TEST_APPEND:-}" ]; then
  printf '%s\n' "$TURN_TEST_APPEND" | tr ';' '\n' >> "$out"
fi

if [ ! -s "$TURN_CERT_FILE" ]; then
  echo "coturn: TLS certificate $TURN_CERT_FILE not present yet; 5349 will be unavailable until certbot issues it" >&2
fi

# Reload TLS certificates when certbot replaces them (coturn: SIGUSR2).
(
  last=$(stat -c %Y "$TURN_CERT_FILE" 2>/dev/null || echo none)
  while sleep 300; do
    cur=$(stat -c %Y "$TURN_CERT_FILE" 2>/dev/null || echo none)
    if [ "$cur" != "$last" ]; then
      # First certificate: restart so the TLS listener opens. Renewal: reload.
      if [ "$last" = "none" ]; then kill -TERM 1; else kill -USR2 1; fi 2>/dev/null || true
    fi
    last=$cur
  done
) &

exec turnserver -c "$out" "$@"
