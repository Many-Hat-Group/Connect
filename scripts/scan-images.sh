#!/bin/sh
# Image scan with Trivy (pinned). Fails on HIGH or CRITICAL vulnerabilities
# that have a fix available. Images: everything docker-compose.yml runs (our
# builds and the hardened wrappers in images/).
#   EXTRA_CA_FILE=/path/ca.pem scripts/scan-images.sh   (builders behind a TLS proxy only)
set -eu
cd "$(dirname "$0")/.."
TRIVY_IMAGE=${TRIVY_IMAGE:-aquasec/trivy:0.74.0}
IMAGES=${IMAGES:-"connect-app:local connect-signal:local connect-nginx:local connect-postgres:local connect-redis:local connect-coturn:local connect-certbot:local"}
SEVERITY=${SEVERITY:-HIGH,CRITICAL}
mkdir -p .trivy-cache
extra=""
if [ -n "${EXTRA_CA_FILE:-}" ]; then
  extra="-v $EXTRA_CA_FILE:/etc/ssl/certs/extra-ca.pem:ro -e SSL_CERT_FILE=/etc/ssl/certs/extra-ca.pem -e HTTPS_PROXY=${HTTPS_PROXY:-} --network host"
fi
fail=0
for img in $IMAGES; do
  echo "== $img"
  # shellcheck disable=SC2086
  docker run --rm $extra -v /var/run/docker.sock:/var/run/docker.sock -v "$PWD/.trivy-cache:/root/.cache/trivy" \
    "$TRIVY_IMAGE" image --quiet --scanners vuln --ignore-unfixed --severity "$SEVERITY" --exit-code 1 \
    --ignorefile /dev/null "$img" || fail=1
done
exit $fail
