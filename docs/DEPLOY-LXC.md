# Deploying Connect on a Proxmox LXC: guide for LLM agents

This is an executable runbook for an AI agent (Claude Code or similar) with shell access to a fresh LXC. A human can follow it too. Work top to bottom. Every step has a **Verify** block: do not move on until it passes. Record results in `docs/AS-BUILT.md` (Section 14 table and the phase tables) as you go.

Related: `docs/RUNBOOK.md` (day-to-day operations), `docs/ARCHITECTURE.md` (what runs where), `docs/edge-proxy-connect.conf` (the drafted edge proxy site).

---

## 0. Rules for the agent (read first, follow always)

1. **Stay inside the LXC.** You may change anything inside the LXC. You must **STOP and ask the owner** before anything that changes something outside it: the edge nginx proxy, DNS, the datacenter or Proxmox firewall, Proxmox host or LXC settings, the RustDesk server.
2. **Never modify `vendor/`** (pinned VDO.Ninja and handshake server). `./scripts/check-vendor.sh` must pass. If something seems to need a vendor change, STOP and report.
3. **Secrets never appear in chat, logs, commit messages or command lines you print.** Do not `cat .env`. Do not echo tokens. Put secrets into files with mode 600 (Section 3). When you show configuration, show only non-secret keys:
   `grep -vE 'PASSWORD|SECRET|KEY|TOKEN' /opt/connect/.env`
4. **Going public needs two explicit confirmations** from the owner, in this order: ask "Confirm 1 of 2", then give a written summary of exactly what becomes reachable from the internet (hostnames, IPs, ports), then ask "Confirm 2 of 2". One "yes" is not enough. Approval for one step does not carry over to the next.
5. **Destructive commands need the owner's approval first:** `docker compose down -v`, `docker volume rm`, deleting `/opt/connect/.env`, restoring a database over live data, `rm -rf` on anything under `/opt/connect` or a Docker volume.
6. **Do not invent values.** Every value in Section 2 comes from the owner or from a command you ran. If one is missing, ask for it.
7. **Report honestly.** If a check fails, say so and include the relevant output, with secrets removed.

---

## 1. What you are deploying

One LXC runs everything under Docker Compose:

| Service | Purpose | Exposure |
|---|---|---|
| router (nginx) | Only HTTP entry point, port 8080 | Internal NIC only, edge proxy IP only |
| app, worker | FastAPI app and background jobs | Docker network only |
| signal | VDO.Ninja handshake server | Docker network only |
| postgres, redis, backup | Data, cache, nightly `pg_dump` | Docker network only |
| coturn (host network) | TURN/STUN for WebRTC | Public: 3478 UDP/TCP, 5349 TCP, 49160 to 49200 UDP |
| certbot | DNS-01 certificate for the TURN host (Cloudflare) | Outbound only |

TLS for the web site is terminated on the owner's existing edge nginx proxy, which forwards to `<LXC internal IP>:8080`.

---

## 2. Inputs you need from the owner

Collect all of these before Section 4. The `.env` key each one fills is shown.

| # | Input | `.env` key | Example | Notes |
|---|---|---|---|---|
| 1 | Public web address | `PUBLIC_URL` | `https://connect.example.com` | Must be https |
| 2 | Public host (host[:port] of the URL) | `PUBLIC_HOST` | `connect.example.com` | |
| 3 | Edge proxy IP (as seen by the LXC) | `TRUSTED_PROXY_IP` | `10.0.10.2` | Only this IP may reach 8080; only it is trusted for `X-Forwarded-For` |
| 4 | LXC internal NIC IP | `ROUTER_BIND_IP` | `10.0.10.20` | 8080 is published only on this IP |
| 5 | TURN host name | `TURN_HOST` | `turn.example.com` | DNS only, never proxied |
| 6 | Networking option | n/a | `a` or `b` | See Section 2.1 |
| 7 | TURN bind, relay and external IPs | `TURN_LISTEN_IP`, `TURN_RELAY_IP`, `TURN_EXTERNAL_IP` | | See Section 2.1 |
| 8 | Datacenter's own IP ranges | `TURN_EXTRA_DENIED_RANGES` | `203.0.113.0-203.0.113.255` | Comma separated `start-end`. TURN refuses to relay into them |
| 9 | Let's Encrypt email | `CERTBOT_EMAIL` | `ops@example.com` | |
| 10 | Cloudflare API token | `CLOUDFLARE_DNS_API_TOKEN` | (secret) | Zone DNS edit, the TURN host's zone only |
| 11 | Git repository and branch | `REPO_URL`, `BRANCH` | `https://github.com/<org>/connect.git`, `main` | Until the build branch is merged, the branch is `claude/loving-newton-v7l17n` |
| 12 | RustDesk server: ID host, public key, relay host | entered in the setup wizard | | Not in `.env` |

### 2.1 Networking options

- **Option a: second NIC with the public IP.** coturn binds to the public IP directly.
  `TURN_LISTEN_IP=TURN_RELAY_IP=TURN_EXTERNAL_IP=<public IP>`
- **Option b: one internal NIC; the datacenter firewall forwards the TURN ports to the LXC.** coturn binds to the internal IP and advertises the public one.
  `TURN_LISTEN_IP=TURN_RELAY_IP=<internal IP>`, `TURN_EXTERNAL_IP=<public IP>/<internal IP>`

Get the IPs with `ip -4 -br addr`. Confirm the option with the owner; never guess it.

---

## 3. Handling secrets

`install.sh` reads the Section 2 values from environment variables. Put them in a root-only file and source it, so they never appear on a command line or in shell history:

```sh
install -m 600 /dev/null /root/connect-install.env
# Open /root/connect-install.env in an editor (or have the owner paste into it) and add:
#   REPO_URL=...  BRANCH=...  PUBLIC_URL=...  PUBLIC_HOST=...  TRUSTED_PROXY_IP=...
#   ROUTER_BIND_IP=...  TURN_HOST=...  TURN_LISTEN_IP=...  TURN_RELAY_IP=...
#   TURN_EXTERNAL_IP=...  TURN_EXTRA_DENIED_RANGES=...  CERTBOT_EMAIL=...
#   CLOUDFLARE_DNS_API_TOKEN=...
```

If the owner sends you the Cloudflare token in chat, tell them it is now stored in the conversation and recommend rotating it after deployment.

`install.sh` generates every other secret itself (database, Redis, session, TOTP encryption key, TURN shared secret, setup token) into `/opt/connect/.env` with mode 600. On a fresh install, the values you supply replace the defaults in `.env.example`. On a re-run, existing values are never overwritten.

**Back up `/opt/connect/.env` with the LXC.** Losing it makes the database volume unreadable (the password no longer matches) and invalidates every user's authenticator.

---

## 4. Preflight checks on the LXC

Run as root.

```sh
. /etc/os-release && echo "$PRETTY_NAME"   # expect Debian GNU/Linux 13
nproc; free -m; df -h /                    # expect 4 vCPU, about 4096 MB, 32 GB
ip -4 -br addr                             # note the internal and (option a) public IPs
cat /proc/1/status | grep -i '^NSpid'      # sanity: running in a container
date -u                                    # clock must be right (TOTP and TLS depend on it)
```

**Verify:** Debian 13; at least 4 GB RAM and 25 GB free; the IPs match what the owner gave you; the clock is within a few seconds of real UTC (if not, STOP: time sync is a Proxmox host matter).

The LXC must be unprivileged with `nesting=1,keyctl=1`. You cannot see that from inside, so ask the owner to confirm it. Docker will fail in Section 5 if nesting is off.

---

## 5. Install

```sh
apt-get update && apt-get install -y git curl ca-certificates
set -a; . /root/connect-install.env; set +a
if [ ! -d /opt/connect/.git ]; then git clone --branch "$BRANCH" "$REPO_URL" /opt/connect; fi
cd /opt/connect && sh install.sh 2>&1 | tee /root/connect-install.log
```

`install.sh` does all of the following, and is safe to re-run:
- installs Docker Engine and the Compose plugin from Docker's apt repository;
- initialises the pinned submodules and runs `check-vendor`;
- creates `.env` (mode 600) with your values and generated secrets;
- adds an in-LXC `DOCKER-USER` rule so only the edge proxy can reach 8080;
- builds the images, starts the stack, waits until every service is healthy;
- prints the one-time setup URL while no admin exists.

The log does not contain secrets apart from the setup URL's token. Treat that URL as a secret: give it only to the owner.

**Verify:**

```sh
cd /opt/connect
docker compose ps --format '{{.Service}} {{.Status}}'      # every service Up, "(healthy)" where shown
./scripts/check-vendor.sh                                 # check-vendor: OK
grep -vE 'PASSWORD|SECRET|KEY|TOKEN' .env | grep -v '^#' | grep .   # values look right
stat -c '%a' .env                                         # 600
```

If `install.sh` stops with "values still needed in .env", add them to `/root/connect-install.env` **and** to `/opt/connect/.env`, then re-run.

---

## 6. Verify the platform (Phase 1 checks on the real LXC)

Run each check and record PASS or FAIL with evidence in `docs/AS-BUILT.md` (Phase 1 table: replace "PENDING (LXC)").

```sh
cd /opt/connect

# 6.1 Router health (inside the router container; independent of the firewall rule)
docker compose exec -T router wget -qO- http://127.0.0.1:8080/healthz      # ok

# 6.2 App health through the router
docker compose exec -T router wget -qO- http://app:8000/healthz            # ok

# 6.3 Only the expected ports listen on non-loopback addresses
python3 tests/integration/listening_ports.py 8080 3478 5349
#   Expect: 8080 on ROUTER_BIND_IP, 3478 tcp/udp, 5349 tcp (once the certificate exists), UDP 49160-49200.
#   Anything else (for example sshd on 22) must be explained to the owner.

# 6.4 TURN: ephemeral credentials work, private and reserved peers refused
make turn-check                      # option a
#   Option b (coturn listens on a private IP, which TURN must refuse as a peer):
#   tests/integration/turn_check.sh <TURN_LISTEN_IP> 1.1.1.1
#   Every line must start with PASS.

# 6.5 TLS certificate for the TURN host
docker compose logs certbot | tail -20      # "certificate for <TURN_HOST> deployed"
docker compose logs coturn | grep -i 5349    # TLS listener opened on 5349 (coturn restarts once after the first certificate)

# 6.6 Backups
make backup
docker compose exec -T backup sh -c 'ls -1 /backups/*.dump | tail -1'
make restore-test FILE=<path printed above>   # restores into a scratch database, then drops it

# 6.7 Idempotency
set -a; . /root/connect-install.env; set +a; sh install.sh >/root/connect-install-2.log 2>&1; echo $?
#   Exit 0; same setup token as before; `iptables -S DOCKER-USER` shows the 8080 rule exactly once.
```

From **outside** the LXC (the owner, or an agent on another host): a port scan of the internal IP shows only 8080 (and only from the proxy IP); a scan of the public IP shows only the TURN ports. Ask the owner to run it if you cannot. This is the Phase 1 acceptance check "only 8080 and the TURN ports respond".

If certbot fails: check the token's zone scope, then the email address. Set `CERTBOT_STAGING=1` in `.env` while debugging (staging certificates are not trusted by browsers), then remove it and run `docker compose up -d certbot`.

---

## 7. First-run setup (the owner, in a browser)

The wizard needs the site reachable over HTTPS at `PUBLIC_URL`, because session cookies are `__Host-` and `Secure`. That requires the edge proxy site (Section 8.1). Two ways to do this before go-live, both of which change the edge proxy, so **STOP and let the owner choose**:

- **Private first (recommended):** enable the proxy site with `allow <owner's IP>; deny all;` inside the `location /` block, and let the owner reach it with a hosts-file entry (`<proxy public IP> connect.example.com`) while public DNS is not yet published. Nothing is public. One owner confirmation is enough for this restricted step, but say clearly what changes.
- **Straight to go-live:** follow Section 9 first.

Then the owner opens the setup URL printed by `install.sh` and completes:
1. the first admin account and authenticator (keep the 10 recovery codes);
2. branding (name, logo, colours);
3. web addresses (Connect, help, privacy);
4. TURN host and RustDesk server details.

The setup link stops working permanently once the admin exists.

**Verify:** `/admin/health` shows every check OK; the **Run test** button for "Relay-only connection" shows OK (relay candidates through TURN). On **Settings, RustDesk**, press **Check now**: the latest stable RustDesk downloads and verifies, or the reason it failed is shown. If GitHub rate-limits the server, add a read-only `GITHUB_TOKEN` to `.env` and run `docker compose up -d app worker`.

Then create the techs under **Users**. Each one gets a temporary password (shown once) and must set up an authenticator at first sign-in.

---

## 8. Changes outside the LXC (owner applies them; you draft and check)

### 8.1 Edge proxy site

The ready-to-use site is `docs/edge-proxy-connect.conf`. Replace `<LXC_INTERNAL_IP>` with `ROUTER_BIND_IP`. Key points: `X-Forwarded-For` must be **overwritten** with `$remote_addr`, not appended; websockets need the `Upgrade` headers; timeouts are 3600 s; `client_max_body_size 5m`.

### 8.2 Firewall

| Direction | Port | Source | Destination |
|---|---|---|---|
| In | 3478 UDP and TCP | Internet | Public IP (TURN) |
| In | 5349 TCP | Internet | Public IP (TURN over TLS) |
| In | 49160 to 49200 UDP | Internet | Public IP (TURN relay) |
| In | 8080 TCP | Edge proxy only | LXC internal IP |
| Out | 443 TCP | LXC | Internet (Docker images, GitHub releases, Let's Encrypt, Cloudflare API) |

Nothing else needs to be open inbound.

### 8.3 DNS

| Record | Points to | Cloudflare proxy |
|---|---|---|
| `connect.<domain>` | Edge proxy | As the owner's proxy setup requires |
| `turn.<domain>` | Public IP | **DNS only** (grey cloud). TURN cannot work through the Cloudflare proxy |

---

## 9. Go-live (double confirmation)

Before asking, make sure every item is done or explicitly waived by the owner:
- Section 6 checks all PASS and are recorded in `docs/AS-BUILT.md`;
- the external port scan is done;
- the manual device matrix (`docs/device-test-matrix.md`) is done;
- all tech accounts exist with authenticators enrolled;
- test data removed (end any test sessions, deactivate test users);
- the Proxmox Backup job includes this LXC, and one `pg_dump` restore has been tested.

Then:

1. Ask exactly: **"Confirm 1 of 2: ready to make Connect reachable from the internet?"**
2. After a yes, post a written summary in this shape (fill in the real values):

   > Becomes public:
   > - `https://connect.<domain>` via the edge proxy at `<proxy public IP>`, TCP 443, forwarding to `<LXC internal IP>:8080`.
   > - `turn.<domain>` at `<public IP>`: 3478 UDP and TCP, 5349 TCP, 49160 to 49200 UDP.
   >
   > Changes: edge proxy site enabled for everyone (allowlist removed), DNS records published, firewall rules applied.
   > Nothing else is exposed: Postgres, Redis, the app and the signal server stay on the internal Docker network.
   > Rollback: disable the proxy site and remove the DNS records.

3. Ask exactly: **"Confirm 2 of 2: apply these changes now?"**
4. Only after the second yes, the owner (or you, if they explicitly ask you to and you have access) applies the changes in Section 8.

**Verify after go-live:** from an external network and from a phone on mobile data, a test customer joins with a real code, shares their screen or camera, and the tech sees it. On `/admin/health`, the relay test passes. Record the date and both confirmations in the Section 14 table of `docs/AS-BUILT.md`.

---

## 10. Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| `docker: ... permission denied` / overlay errors at start | LXC lacks `nesting=1` (or `keyctl=1`), or the storage cannot do overlay | STOP: ask the owner to check the LXC options. `docker info \| grep -i 'storage driver'` shows what Docker picked |
| `toomanyrequests` / HTTP 429 pulling images | Docker Hub anonymous rate limit | Wait and re-run `install.sh`, or have the owner `docker login` with a Hub account |
| `failed to create network ... Pool overlaps` | `DOCKER_SUBNET` clashes with a LAN range | Pick another private /24 for `DOCKER_SUBNET`, `DOCKER_IP_RANGE` (its upper /25) and `ROUTER_IP` (in the lower half) in `.env`, then `docker compose up -d` |
| app unhealthy, logs say `password authentication failed` | `.env` was regenerated while the old `pgdata` volume remained | Restore the original `.env` from backup. Do not delete volumes without the owner's approval |
| Port 5349 not listening | No TURN certificate yet | Check `docker compose logs certbot`. coturn restarts itself after the first certificate |
| Relay test fails, 3478 works locally | Firewall or forwarding not in place, or wrong `TURN_EXTERNAL_IP` | Re-check Section 2.1 and 8.2 with the owner. `make turn-check` from inside the LXC isolates coturn from the firewall |
| Customers' video never connects behind strict firewalls | Only UDP open, or 5349 not reachable | Confirm 3478 TCP and 5349 TCP are open; check the TURN certificate |
| Everyone appears to come from one IP | Proxy not overwriting `X-Forwarded-For`, or `TRUSTED_PROXY_IP` wrong | Fix the proxy header (Section 8.1) or the IP in `.env`, then `docker compose up -d router` |
| `check-vendor` fails | Something changed under `vendor/` | Do not fix by editing. `git -C vendor/vdo.ninja status`, then restore with `git submodule update --init --force` and report to the owner |

Logs: `docker compose logs -f <service>`. Never paste `.env` contents into a report.

---

## 11. Handing over

When you finish, give the owner:
- the Section 6 results and anything still PENDING;
- the completed Section 14 table in `docs/AS-BUILT.md` (LXC ID, IPs, networking option, versions, certificate method, backup job, go-live date);
- the reminder to store `/opt/connect/.env` safely and to keep the Proxmox Backup job running;
- a pointer to `docs/RUNBOOK.md` for upgrades, secret rotation, restores and the RustDesk mirror.

Commit documentation updates (never `.env`) and push them to the owner's branch if they ask you to.
