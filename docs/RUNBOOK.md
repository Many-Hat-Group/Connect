# Connect: runbook

First install: see `docs/DEPLOY-LXC.md`.

All commands run on the LXC as root from `/opt/connect`.

## Everyday

| Task | Command |
|---|---|
| Status | `docker compose ps` |
| Logs (all, or one service) | `docker compose logs -f --tail=200` / `docker compose logs -f app` |
| Stop / start | `docker compose down` / `docker compose up -d` |
| Health in the browser | Settings, System health (includes a relay-only TURN test) |
| One-off backup | `make backup` |

## Upgrading the app

```
git pull
./scripts/check-vendor.sh
docker compose build --pull=false
docker compose up -d          # the app runs "alembic upgrade head" on start
make test-unit && make scan
```

Or re-run `install.sh` (idempotent). Check System health afterwards.

## Upgrading VDO.Ninja (never patch it)

1. `cd vendor/vdo.ninja && git fetch --tags && git checkout <new tag> && cd ../..`
2. Update the commit in `app/content/licences.yaml` and the table in `docs/vdo-notes.md` (`check-vendor` fails until you do).
3. Re-run the Phase 0 suite: `make phase0-up test-phase0 phase0-down`. Every test must pass; if a parameter changed, update `app/services/vdo.py` and `docs/vdo-notes.md`.
4. Run `make test-e2e` and the manual device matrix (`docs/device-test-matrix.md`).
5. Commit with the new tag in the message.

The handshake server (`vendor/websocket_server`) is upgraded the same way.

## Rotating secrets

All secrets live in `.env` (mode 600). After editing, `docker compose up -d` recreates the affected services.

| Secret | Effect of rotating | Steps |
|---|---|---|
| `TURN_SHARED_SECRET` | Live sessions lose TURN until they reconnect | `openssl rand -hex 32`, edit `.env`, `docker compose up -d coturn app worker` |
| `SESSION_SECRET` | None today (sessions are in Redis) | edit and recreate app |
| `TOTP_ENCRYPTION_KEY` | Existing authenticator secrets cannot be decrypted: **every user must re-enrol** | Avoid. If compromised: set a new Fernet key, recreate app, then reset MFA for every user in Users |
| `POSTGRES_PASSWORD` | DB login | `docker compose exec postgres psql -U connect -c "ALTER USER connect PASSWORD '<new>'"`, then edit `.env`, `docker compose up -d` |
| `REDIS_PASSWORD` | Everyone signed out | edit `.env`, `docker compose up -d` |
| Cloudflare token | Certificate renewals | edit `.env`, `docker compose up -d certbot` |

Losing `.env` means the database volume's password no longer matches: restore `.env` from the Proxmox backup first.

## Restore

- **From `pg_dump`:** `docker compose exec backup ls /backups`, then `docker compose stop app worker`, then `docker compose exec backup sh /opt/backup/restore.sh /backups/<file>.dump`, then `docker compose up -d`. Test a restore without touching live data: `make restore-test FILE=/backups/<file>.dump`.
- **From Proxmox Backup:** restore the whole LXC, then `docker compose up -d` and check System health.

## RustDesk mirror

Settings, RustDesk: **Check now** (sync), **Download again**, **Approve and activate** (releases without a GitHub digest wait here), **Pin this version** (new releases are downloaded but not activated), **Roll back to previous**. A hash mismatch never replaces the active version; see the `rustdesk.sync_failed` audit event. If the GitHub API rate-limits the server, set `GITHUB_TOKEN` (read-only, no scopes) in `.env`.

If the RustDesk key contains `/` (or another character not allowed in Windows file names), the Windows download falls back to `rustdesk.exe` and the RustDesk page shows a warning; customers then type the server details shown in the prompt.

## Join code brute-force banner

1. Audit log, filter Event "All code", last day: look at `code.failed_attempt` and `code.ip_locked` IPs.
2. Single IPs are already locked out for 15 minutes automatically.
3. For a sustained attack, block the IPs or ranges at the edge proxy (`deny` in the Connect site) and tell Pierce.
4. The banner clears itself an hour after the last threshold breach.

## Widening the TURN relay range

1. In `.env` set `TURN_MIN_PORT` / `TURN_MAX_PORT` (each relayed allocation needs one port; about 40 per 41 ports).
2. Open the same UDP range on the datacenter firewall (outside the LXC: Pierce).
3. `docker compose up -d coturn`, then run the relay test on System health and `make turn-check`.

## Known limits

- Revoking a tech's access closes their viewer; a tampered browser could keep an already-established peer connection until it drops. Kill ends it for everyone.
- Framerate presets apply to every viewer of a stream (publisher-side setting).
