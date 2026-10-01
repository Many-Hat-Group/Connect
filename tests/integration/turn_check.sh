#!/bin/sh
# Phase 1 TURN checks against the running coturn, using an ephemeral
# credential built from TURN_SHARED_SECRET in .env.
#   tests/integration/turn_check.sh <turn-ip> [public-peer-ip]
# Expect: public peer allocation succeeds; every private/reserved peer
# returns 403 (Forbidden IP); a bad password cannot allocate.
set -eu
cd "$(dirname "$0")/../.."
TURN=${1:?turn server ip}
PUBLIC_PEER=${2:-$TURN}
SECRET=$(grep -E '^TURN_SHARED_SECRET=' .env | cut -d= -f2-)
IMG=coturn/coturn:4.7.0
fail=0
cred() { U="$(( $(date +%s) + 3600 )):check-$1-$$"; P=$(printf '%s' "$U" | openssl dgst -sha1 -hmac "$SECRET" -binary | base64); }
uc() { docker run --rm --network host --entrypoint turnutils_uclient "$IMG" -u "$U" -w "$P" -n 1 -m 1 -l 100 -e "$1" -r 3480 "$TURN" 2>&1; }

cred pub
out=$(uc "$PUBLIC_PEER")
if echo "$out" | grep -q "tot_send_msgs=[1-9]"; then echo "PASS relay allocation to public peer $PUBLIC_PEER"; else echo "FAIL relay allocation to $PUBLIC_PEER"; echo "$out" | head -5; fail=1; fi

n=0
for peer in 10.1.2.3 172.16.5.5 192.168.1.10 127.0.0.1 100.64.0.1 169.254.1.1 0.1.2.3 198.18.0.1; do
  n=$((n + 1)); cred "priv$n"
  if uc "$peer" | grep -q "403"; then echo "PASS private peer $peer refused (403)"; else echo "FAIL private peer $peer NOT refused"; fail=1; fi
done

U="$(( $(date +%s) + 3600 )):check-bad-$$"; P=not-the-password
if uc "$PUBLIC_PEER" | grep -q "Cannot complete Allocation"; then echo "PASS bad credential refused"; else echo "FAIL bad credential accepted"; fail=1; fi

U="$(( $(date +%s) - 60 )):check-expired-$$"; P=$(printf '%s' "$U" | openssl dgst -sha1 -hmac "$SECRET" -binary | base64)
if uc "$PUBLIC_PEER" | grep -q "Cannot complete Allocation"; then echo "PASS expired credential refused"; else echo "FAIL expired credential accepted"; fail=1; fi
exit $fail
