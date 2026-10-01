#!/bin/sh
# Bootstrap Connect on a fresh Debian 13 LXC (unprivileged, nesting=1, keyctl=1).
# Safe to re-run: it only installs what is missing, never overwrites secrets,
# and brings the stack up to the current configuration.
#
#   REPO_URL=https://github.com/many-hat-group/connect.git BRANCH=main \
#   PUBLIC_URL=https://connect.mhits.au PUBLIC_HOST=connect.mhits.au \
#   TRUSTED_PROXY_IP=10.0.0.2 ROUTER_BIND_IP=10.0.0.20 \
#   TURN_HOST=turn.mhits.au TURN_LISTEN_IP=203.0.113.10 TURN_RELAY_IP=203.0.113.10 TURN_EXTERNAL_IP=203.0.113.10 \
#   CERTBOT_EMAIL=ops@example.com CLOUDFLARE_DNS_API_TOKEN=... sh install.sh
set -eu

APP_DIR=${APP_DIR:-/opt/connect}
REPO_URL=${REPO_URL:-}
BRANCH=${BRANCH:-main}
SKIP_DOCKER_INSTALL=${SKIP_DOCKER_INSTALL:-0}
SKIP_FIREWALL=${SKIP_FIREWALL:-0}
SKIP_BUILD=${SKIP_BUILD:-0}  # build images elsewhere (CI, or a builder behind a proxy)

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root"

# 1. Docker Engine and the Compose plugin from Docker's apt repository.
if [ "$SKIP_DOCKER_INSTALL" != 1 ] && ! docker compose version >/dev/null 2>&1; then
  say "Installing Docker Engine and Compose plugin"
  . /etc/os-release
  [ "$ID" = debian ] || die "expected Debian, found $ID"
  apt-get update
  apt-get install -y ca-certificates curl git gnupg openssl iptables
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $VERSION_CODENAME stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi
command -v git >/dev/null || { apt-get update && apt-get install -y git; }
command -v openssl >/dev/null || { apt-get update && apt-get install -y openssl; }

# 2. Repository and pinned submodules.
if [ ! -d "$APP_DIR/.git" ]; then
  [ -n "$REPO_URL" ] || die "set REPO_URL to clone into $APP_DIR (or copy the repo there first)"
  say "Cloning $REPO_URL ($BRANCH) into $APP_DIR"
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"
say "Initialising pinned submodules"
git submodule update --init --recursive
./scripts/check-vendor.sh

# 3. .env: create from the example, fill supplied values, generate secrets.
FRESH_ENV=0
if [ ! -f .env ]; then
  say "Creating .env"
  cp .env.example .env
  FRESH_ENV=1
fi
chmod 600 .env

set_env() { # key value [force]: set only if empty, or if force=1
  key=$1; val=$2; force=${3:-0}
  cur=$(grep -E "^$key=" .env | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//')
  if { [ -z "$cur" ] || [ "$force" = 1 ]; } && [ -n "$val" ]; then
    if grep -qE "^$key=" .env; then
      esc=$(printf '%s' "$val" | sed 's/[\/&|]/\\&/g')
      sed -i "s|^$key=.*|$key=$esc|" .env
    else
      printf '%s=%s\n' "$key" "$val" >> .env
    fi
  fi
}
for key in PUBLIC_URL PUBLIC_HOST TRUSTED_PROXY_IP ROUTER_BIND_IP TURN_HOST TURN_LISTEN_IP TURN_RELAY_IP \
           TURN_EXTERNAL_IP TURN_EXTRA_DENIED_RANGES CERTBOT_EMAIL CLOUDFLARE_DNS_API_TOKEN; do
  eval "v=\${$key:-}"
  # On a fresh .env, supplied values replace the example defaults. On
  # re-runs, existing values are never overwritten.
  set_env "$key" "$v" "$FRESH_ENV"
done
# Strip inline comments from the example's placeholder lines.
sed -i -E 's/^([A-Z_]+=[^#[:space:]]*)[[:space:]]+#.*$/\1/; s/^([A-Z_]+=)[[:space:]]*#.*$/\1/' .env
set_env POSTGRES_PASSWORD "$(openssl rand -hex 24)"
set_env REDIS_PASSWORD "$(openssl rand -hex 24)"
set_env SESSION_SECRET "$(openssl rand -hex 32)"
set_env TURN_SHARED_SECRET "$(openssl rand -hex 32)"
set_env SETUP_TOKEN "$(openssl rand -hex 24)"
# Fernet key: 32 random bytes, urlsafe base64.
set_env TOTP_ENCRYPTION_KEY "$(openssl rand 32 | base64 | tr '+/' '-_')"

missing=""
for key in PUBLIC_URL PUBLIC_HOST TRUSTED_PROXY_IP ROUTER_BIND_IP TURN_HOST TURN_LISTEN_IP TURN_RELAY_IP TURN_EXTERNAL_IP; do
  v=$(grep -E "^$key=" .env | cut -d= -f2-)
  [ -n "$v" ] || missing="$missing $key"
done
[ -z "$missing" ] || die "values still needed in $APP_DIR/.env:$missing (edit it, then re-run install.sh)"

# 4. In-LXC defence in depth: only the edge proxy may reach 8080. The
#    datacenter firewall remains the primary control.
if [ "$SKIP_FIREWALL" != 1 ] && command -v iptables >/dev/null 2>&1; then
  proxy=$(grep -E '^TRUSTED_PROXY_IP=' .env | cut -d= -f2-)
  iptables -N DOCKER-USER 2>/dev/null || true
  if ! iptables -C DOCKER-USER -p tcp --dport 8080 ! -s "$proxy" -m conntrack --ctorigdstport 8080 -j DROP 2>/dev/null; then
    say "Restricting 8080 to $proxy (DOCKER-USER)"
    iptables -I DOCKER-USER -p tcp --dport 8080 ! -s "$proxy" -m conntrack --ctorigdstport 8080 -j DROP || \
      echo "install.sh: could not add DOCKER-USER rule (continuing; rely on the datacenter firewall)"
  fi
fi

# 5. Build and start.
if [ "$SKIP_BUILD" != 1 ]; then
  say "Building images"
  docker compose build --pull=false
fi
say "Starting services"
docker compose up -d --remove-orphans

say "Waiting for services to become healthy"
i=0
while :; do
  unhealthy=$(docker compose ps --format '{{.Service}} {{.Health}}' | awk '$2 != "" && $2 != "healthy" {print $1}')
  [ -z "$unhealthy" ] && break
  i=$((i + 1)); [ $i -gt 60 ] && die "not healthy after 5 minutes: $unhealthy (docker compose logs $unhealthy)"
  sleep 5
done
docker compose ps

# 6. One-time setup URL while no admin exists.
url=$(grep -E '^PUBLIC_URL=' .env | cut -d= -f2-)
if docker compose exec -T app python -m app.cli needs-setup >/dev/null 2>&1; then
  tok=$(grep -E '^SETUP_TOKEN=' .env | cut -d= -f2-)
  say "Setup is not complete. Open this one-time URL (it stops working once the first admin exists):"
  echo "    $url/setup?token=$tok"
else
  say "Setup already complete. Portal: $url/portal"
fi
