# Connect: as-built record

Update this file as each phase completes, then upload it to the wiki.

## Section 14 record

| Item | Value |
|---|---|
| LXC ID and hostname | _pending (Pierce provisions)_ |
| Internal IP | _pending_ |
| Public IP (TURN) | _pending_ |
| Networking option used (a or b) | _pending_ |
| VDO.Ninja tag | v30.2 (`d5a8b5669d1cb0ff75f984499f7025b745657808`) |
| Handshake server version | steveseguin/websocket_server `92d9bce4f8c798b520cfd2781d6f7f8ae607b3fb` (`vdoninja_advanced.js`), ws 8.22.0, uuid 14.0.2, node 22.20.0-alpine |
| coturn image version | `coturn/coturn:4.7.0` |
| App version / git commit | see `git log` (branch `claude/loving-newton-v7l17n`) |
| RustDesk server host and relay | _pending (RustDesk server playbook)_ |
| Current RustDesk client version | _pending (Phase 7)_ |
| Cert renewal method for turn.mhits.au | certbot DNS-01 (Phase 1) |
| Backup job and schedule | _pending_ |
| Go-live date and confirmations | _not yet_ |
| Deviations from this playbook | see below |

## Pinned images

| Service | Image |
|---|---|
| router | `nginx:1.28.0-alpine` |
| signal | built from `node:22.20.0-alpine` |
| coturn | `coturn/coturn:4.7.0` |

## Phase results

### Phase 0: Verification spike (2026-10-01)

Environment: Claude Code cloud container (not the LXC). Docker 29.6, Chromium 141 via Playwright 1.56 with fake media. A test-only TLS edge (`tests/phase0/edge.conf`) stands in for Pierce's proxy, and a stub app (`tests/phase0/authstub.py`) validates signal tokens.

| Acceptance check | Result | Evidence |
|---|---|---|
| `docs/vdo-notes.md` lists every parameter and event, with confirmed name and behaviour | PASS | `docs/vdo-notes.md`, `docs/vdo-source-research.md` |
| Screen and camera publish-and-view with zero external network requests | PASS | `test_publish_and_view_fully_self_hosted[screen,camera]`; network logs `docs/phase0/*.netlog.json` (all requests to our origin, zero CSP violations, ICE servers only our coturn) |
| Relay-only connection through coturn | PASS | `test_relay_only_through_coturn` (all nominated pairs are relay candidates; coturn log shows ephemeral user `<expiry>:<id>` allocations) |
| Token on the `wss` URL works | PASS (no fallback needed) | `test_signal_token_reaches_auth_and_bad_token_is_refused`: token arrives intact at `auth_request`; bad token gets 403 and the publisher never seeds |
| `make check-vendor` passes | PASS | `scripts/check-vendor.sh` (also shown to fail on an untracked file in the submodule) |

Full suite: `make test-phase0`: 14 passed.

## Deviations and decisions

| # | Playbook said | Built | Why |
|---|---|---|---|
| D1 | `&wss=` for the handshake server | `&wss2=` | `wss=` switches VDO.Ninja to stateless relay mode; `vdoninja_advanced.js` needs `customWSS=false`. |
| D2 | `&turn=` (and TLS variant) | `stun=` + `addstun=` + `turn=` slots, all with credentials | VDO.Ninja accepts one URL per slot. Gives 3478 UDP, 3478 TCP and 5349 TLS, and removes Google/Cloudflare STUN. |
| D3 | `&view=` | `&view=` + `&scene` | Required when a room is used. |
| D4 | Room random 32+ chars | 30 random alphanumerics + 32 char password | VDO.Ninja truncates room names to 30 chars; both are hashed together, so entropy is unaffected. |
| D5 | All parameters in the URL | All parameters in the URL **fragment** | Keeps secrets out of server logs and Referer. |
| D6 | Viewer quality values incl. framerate | Framerate is publisher-only | No viewer-side fps request exists in v30.2. Phase 4 decides the publisher path. |
| D7 | (not specified) | Tech viewer iframe is sandboxed (`allow-scripts` only) | Security: VDO.Ninja's iframe API accepts `eval` with no origin check. Opaque origin isolates it from the portal. |
| D8 | (not specified) | `/vdo/` refuses top-level navigation, sends `X-Frame-Options: SAMEORIGIN` and `Access-Control-Allow-Origin: *` | Same finding as D7. |
| D9 | `autostart` for screen share | No `autostart` for screen share in production | `getDisplayMedia` needs a click inside the iframe on real browsers. |
| D10 | uvicorn `--forwarded-allow-ips=<PROXY_IP>` | Router trusts the edge proxy IP (`set_real_ip_from`); uvicorn trusts only the router's fixed Docker IP | The app's direct peer is the router, not the edge proxy. |
| D11 | Docker Hub images | Same official images; this build container pulled them via `mirror.gcr.io` because of Docker Hub rate limits | No effect on the LXC. |

## Open items for Pierce

- **Licence, resale**: `vendor/vdo.ninja/webrtc.js` carries an "All Rights Reserved, cannot be modified without permission" header rather than AGPL. We never modify it, so self-hosting is fine. Before selling installs to other MSPs, confirm with the author or legal that redistributing it unmodified inside a commercial product is acceptable.
- **Security, recommended later**: serve VDO.Ninja from its own hostname (for example `vdo.connect.mhits.au`) to remove the shared-origin risk on the customer side completely. Needs DNS and edge proxy changes (outside this LXC).
