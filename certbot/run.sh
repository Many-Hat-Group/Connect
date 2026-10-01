#!/bin/sh
# Issues and renews the TURN TLS certificate with DNS-01 (Cloudflare), then
# copies it into the shared turncerts volume where coturn picks it up.
set -eu
: "${TURN_HOST:?}"
CREDS=/etc/letsencrypt/cloudflare.ini
OUT=/certs

deploy() {
  live="/etc/letsencrypt/live/$TURN_HOST"
  [ -s "$live/fullchain.pem" ] || return 0
  cp "$live/fullchain.pem" "$OUT/fullchain.pem.new"
  cp "$live/privkey.pem" "$OUT/privkey.pem.new"
  # coturn runs as nobody (65534).
  chown 65534:65534 "$OUT/"*.new; chmod 0600 "$OUT/privkey.pem.new"; chmod 0644 "$OUT/fullchain.pem.new"
  mv "$OUT/fullchain.pem.new" "$OUT/fullchain.pem"
  mv "$OUT/privkey.pem.new" "$OUT/privkey.pem"
  echo "certbot: certificate for $TURN_HOST deployed"
}

if [ -z "${CLOUDFLARE_DNS_API_TOKEN:-}" ] || [ -z "${CERTBOT_EMAIL:-}" ]; then
  echo "certbot: CLOUDFLARE_DNS_API_TOKEN or CERTBOT_EMAIL not set; TURN TLS (5349) stays disabled. Sleeping."
  exec sleep infinity
fi

umask 077
printf 'dns_cloudflare_api_token = %s\n' "$CLOUDFLARE_DNS_API_TOKEN" > "$CREDS"

if [ ! -s "/etc/letsencrypt/live/$TURN_HOST/fullchain.pem" ]; then
  certbot certonly --non-interactive --agree-tos --email "$CERTBOT_EMAIL" \
    --dns-cloudflare --dns-cloudflare-credentials "$CREDS" --dns-cloudflare-propagation-seconds 30 \
    --key-type ecdsa -d "$TURN_HOST" ${CERTBOT_STAGING:+--staging}
fi
deploy

while :; do
  sleep 43200
  certbot renew --non-interactive --dns-cloudflare-credentials "$CREDS" && deploy || echo "certbot: renew failed"
done
