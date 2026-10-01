# Connect: architecture

Browser-based first-line support: customers share a screen or camera with a technician without installing anything, and can be escalated to RustDesk remote control. One LXC per company, fully white-labelled.

## Components (one LXC, Docker Compose)

| Service | Image | Role |
|---|---|---|
| router | `images/nginx` (nginx 1.30.5) | Only entry point (8080 on the internal NIC). `/` and `/ws/*` to the app, `/vdo/` static VDO.Ninja (read-only, own CSP, frame-only), `/signal` to the handshake server behind `auth_request` |
| app | `Dockerfile` (Python 3.12, FastAPI, uvicorn) | Portal, customer pages, JSON API, websockets, downloads |
| worker | same image, `python -m app.worker` | Sweeper (timeouts, every 15 s), retention (daily), RustDesk sync (every 6 h) |
| signal | `signal/Dockerfile` (Node 22) | Unmodified `vdoninja_advanced.js` |
| postgres | `images/postgres` (16.15) | Data, internal network only |
| redis | `images/redis` (7.4.11) | Web sessions, tokens, presence, rate limits, pub/sub fan-out |
| coturn | `images/coturn` (4.18.0), host network | TURN/STUN on the public IP: 3478 udp/tcp, 5349 tcp, 49160 to 49200 udp |
| certbot | `images/certbot` (5.8.0, Cloudflare DNS-01) | `turn.<domain>` certificate for 5349 |
| backup | `images/postgres` | Nightly `pg_dump`, 14 days |

## Request flow

```
Internet -> edge nginx proxy (TLS) -> router:8080 -> app:8000 | /vdo/ static | /signal -> signal:8443
Browser <-> browser media: WebRTC peer to peer, or relayed by coturn on the public IP
```

- The router trusts `X-Forwarded-For` only from `TRUSTED_PROXY_IP`; uvicorn trusts only the router's fixed Docker address.
- The app builds every VDO.Ninja URL server-side, with all parameters in the URL fragment: random room (30 chars) and password (40), random stream IDs, a per-participant signal token, and ephemeral TURN credentials (`<expiry>:<session>`, HMAC-SHA1, 4 h).
- Customer publisher frames run same-origin (capture needs it). Tech viewer frames are sandboxed without `allow-same-origin`, so VDO.Ninja runs in an opaque origin there.
- Each viewer is a separate peer connection from the customer (mesh). Thumbnails are 320 px at about 150 kbps and only run while visible; viewers per stream are capped (default 3).

## Code map

```
app/main.py            app factory, routers, error pages
app/web.py             security headers, CSRF, sessions, auth dependencies, rendering
app/auth/              provider interface, local provider, passwords, TOTP, web sessions, rate limits
app/services/          sessions, codes, vdo (URL builder), turn_creds, presence, viewers, permissions,
                       events (emit = audit + Redis), access (sharing), lifecycle, rustdesk, branding, health
app/api/               HTML and JSON routes (customer, portal, tech API, admin pages, RustDesk, licences)
app/ws/                hub (Redis pub/sub fan-out), portal and customer websockets
app/static/js/         publisher.js, viewer.js, portal.js, dashboard.js, session-view.js, customer-*.js
app/content/           instructions.yaml, licences.yaml
app/defaults.yaml      the ONLY place default brand values live (plus .env)
```

## Extension points (kept for later)

- `events.emit()` writes the audit row and publishes on Redis channel `events`: subscribe a webhook sender there.
- `AuthProvider` (`app/auth/base.py`): add Entra ID next to `LocalAuthProvider`; `users.auth_provider` and `external_id` exist.
- Instructions are data (`instructions.yaml`); moving them into the database is a loader change.
