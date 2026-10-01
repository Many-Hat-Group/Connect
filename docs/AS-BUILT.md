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
| Cert renewal method for turn.mhits.au | `certbot/dns-cloudflare:v5.8.0`, DNS-01, renew check every 12 h, copied to the `turncerts` volume, coturn reloads |
| Backup job and schedule | `backup` service: `pg_dump` custom format daily at 02:30 UTC, 14-day retention, `backups` volume. Proxmox Backup job: _pending_ |
| Go-live date and confirmations | _not yet_ |
| Deviations from this playbook | see below |

## Pinned images

| Service | Image |
|---|---|
| router | `nginx:1.28.0-alpine` |
| signal | built from `node:22.20.0-alpine` |
| coturn | `coturn/coturn:4.7.0` |
| app, worker | built from `python:3.12.11-slim-bookworm` (`requirements.lock`) |
| postgres, backup | `postgres:16.10-alpine` |
| redis | `redis:7.4.6-alpine` |
| certbot | `certbot/dns-cloudflare:v5.8.0` |

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

### Phase 1: LXC and base platform (2026-10-01)

Simulated LXC: a fresh copy of the repository in the build container, `install.sh` run with `SKIP_DOCKER_INSTALL=1 SKIP_BUILD=1` (Docker already present; images pre-built because this container reaches registries through a TLS-intercepting proxy) and a TLS edge stand-in (`tests/lxc-sim/compose.sim.yml`). Not yet run on the real LXC.

| Acceptance check | Result | Evidence |
|---|---|---|
| `install.sh` on a clean LXC brings everything up healthy with no manual steps beyond `.env` values Pierce supplies | PASS (simulated), PENDING (LXC) | All 10 services healthy; prints the one-time setup URL. `.env` mode 600, secrets generated. |
| From outside the LXC, only 8080 (internal NIC) and the TURN ports respond | PASS (simulated), PENDING (LXC external scan) | `tests/integration/listening_ports.py`: only 3478 tcp/udp, the relay range, and 8080 on `ROUTER_BIND_IP`. Postgres, Redis, app and signal have no published ports. In-LXC `DOCKER-USER` rule drops 8080 from anything but the proxy IP. 5349 opens once certbot has a certificate. |
| `turnutils_uclient` gets relay candidates using an ephemeral credential | PASS | `tests/integration/turn_check.sh` (`make turn-check`) |
| A TURN allocation attempt to a private IP peer is refused | PASS | Same script: 10/8, 172.16/12, 192.168/16, 127/8, 100.64/10, 169.254/16, 0/8, 198.18/15 all return 403 Forbidden IP; bad and expired credentials refused |
| Re-running `install.sh` is safe (idempotent) | PASS | Second run: same secrets and setup token, firewall rule not duplicated, stack unchanged and healthy |

Backups: `make backup` wrote a custom-format dump; `make restore-test FILE=...` restored it into a scratch database (the full restore drill is a go-live item).

### Phase 2: App skeleton, auth, roles, setup wizard (2026-10-01)

| Acceptance check | Result | Evidence |
|---|---|---|
| A user cannot reach any portal page without completing TOTP enrolment | PASS | `tests/integration/test_phase2_auth.py::test_password_ok_but_no_totp_cannot_reach_portal` (pages and portal websocket), `test_mfa_pending_cannot_reach_portal`, `test_enrolment_flow_then_portal` |
| Wizard is unreachable without the token and permanently disabled after completion | PASS | `test_wizard_requires_token_and_locks_after_admin` (404 without or with a wrong token; token dead for everyone once the admin exists; later steps 404 after finishing); browser run `tests/e2e/test_phase2_portal.py::test_wizard_end_to_end_in_browser` |
| Login rate limiting and lockout work and are audited | PASS | `test_login_rate_limit_per_account_and_audit` (5 per account), `test_login_rate_limit_per_ip` (10 per IP), `test_mfa_failures_lock_out`; `auth.login_failed`, `auth.mfa_failed`, `auth.locked_out` rows |
| Unit tests for password hashing, TOTP, recovery codes and CSRF pass | PASS | `tests/unit/test_auth_primitives.py`, `tests/unit/test_csrf.py` |
| Changing the brand colour in settings changes customer and portal pages without a code change | PASS | `test_brand_colour_changes_pages_without_code_change` (`/brand.css` served from settings); browser check of the computed top bar colour after the wizard |

Also covered: TOTP replay rejected, single-use recovery codes, session id rotation at each privilege change, `__Host-connect` cookie flags (Secure, HttpOnly, SameSite=Lax, Path=/, no Domain), admin user management with the last-admin guard, deactivated users signed out immediately, strict CSP on app pages with zero violations in the browser, Redis pub/sub fan-out across processes (`tests/integration/test_hub.py`).

Totals: 28 unit, 20 integration, 2 browser e2e.

### Phase 3: Customer flow (2026-10-01)

| Acceptance check | Result | Evidence |
|---|---|---|
| E2E: customer enters a valid code and details, shares (fake media), the session goes to `live` and `audio_available` is recorded | PASS | `tests/e2e/test_phase3_customer.py::test_screen_share_goes_live_and_records_audio` (customer presses VDO.Ninja's own Select screen to share button inside the frame) |
| Bad, expired and used codes give the same response. Sixth failure from an IP is locked out and audited | PASS | `tests/integration/test_phase3_customer.py::test_bad_expired_and_used_codes_look_identical` (same status and message), `test_sixth_failure_from_ip_is_locked_and_audited` (5 failures lock the IP; the right code is then refused; `code.failed_attempt` x5 and `code.ip_locked`); global alert `test_global_bruteforce_banner` |
| On a simulated iPhone UA and missing `getDisplayMedia`, Share Screen is hidden and camera works | PASS | `test_iphone_gets_camera_only` (session recorded as ios/safari/camera, live) |
| Ending from the customer side releases the capture (track `readyState` is `ended`) and the session ends with `customer_ended` | PASS | same screen test: every captured track `ended`, `end_reason = customer_ended`, old customer token refused |
| Instructions render for every key in the YAML (snapshot test) | PASS | `tests/unit/test_phase3_units.py::test_instructions_snapshot` (30 keys, `tests/unit/snapshots/instructions.txt`; also checks no dash punctuation) and `test_required_combinations_resolve` |

Also: customer token cookie `__Host-connect-cust` (HttpOnly, Secure, SameSite=Strict), double-submit CSRF plus origin check on the entry form, `/join/ABCD` prefill, letters I, L and O rejected in the code boxes with a hint, UA and Client Hints detection (iPadOS spotted by touch points), WCAG AA contrast for the fixed palette (all pairs 5.7:1 or better), phone layout checked at 390 px.

Totals so far: 74 unit, 27 integration, 5 browser e2e (plus 14 Phase 0).

### Phase 4: Tech dashboard and session view (2026-10-01)

Measurements are taken on the customer side, on the publisher's outbound RTP to the tech viewer (`getStats`), because the tech viewer runs in a sandboxed opaque-origin frame the test cannot reach.

| Acceptance check | Result | Evidence |
|---|---|---|
| E2E: tech creates a code, customer joins, tile appears with a thumbnail within 5 s of the stream going live | PASS | `tests/e2e/test_phase4_dashboard.py::test_tile_thumbnail_within_5s_and_session_view` (code created with the New session button; thumbnail `new-video-track-added` within 5 s; thumbnail encodes at 320 px or less) |
| Full view opens at native resolution. Switching presets visibly changes resolution and framerate (`getStats`) | PASS | Same test: outbound width equals the source track width (1920); Smooth halves it; Clear drops the framerate to 5 fps or less; Balanced raises it again; `session.quality_changed` audited |
| Audio is muted on open and plays after unmute (fake audio track) | PASS | `test_audio_starts_muted_and_unmutes`: audio track received, `videoVolume` 0 on open, 0.8 after Unmute; `session.audio_unmuted` audited |
| Hidden dashboard tab pauses thumbnails (no inbound video bytes for that viewer) | PASS | `test_hidden_tab_pauses_thumbnails`: on `visibilitychange` to hidden the thumbnail viewer is closed, the publisher's connection to it disappears; it comes back when visible |
| Kill ends the session immediately, the customer page tears down, and reconnecting with the old token fails | PASS | `test_kill_tears_down_customer_and_old_token_fails`: customer page shows Session ended, all capture tracks `ended`, `end_reason = tech_killed`; old token refused on `/s` and `/ws/customer`; optional 1-hour IP block refuses a new code from that IP |

Also: viewer cap enforced server-side (4th viewer gets 409, `tests/integration/test_phase4_api.py`), opening the full view closes that tech's dashboard thumbnail in every tab, `session.viewed` / `session.view_closed` / `session.admin_joined` audited, one portal websocket per page (regression test).

Bugs found and fixed by these tests: IP block stored a UUID in Redis (500 on Kill; the block now happens before any database change); capture Permissions-Policy missed `/s`; `portal.js` loaded twice (duplicate toasts).

Totals: 74 unit, 30 integration, 10 browser e2e (plus 14 Phase 0).

### Phase 5: Sharing, invites, admin oversight (2026-10-01)

| Acceptance check | Result | Evidence |
|---|---|---|
| Permission matrix: tech B gets 404 for tech A's private session over REST and websocket. Sees it after `all_techs` or an accepted invite. Loses it after the invite is revoked | PASS | `tests/integration/test_phase5_permissions.py`: 10 REST endpoints and the session page all 404 for B (B's End and Kill change nothing); websocket presence marking and update fan-out both refused; access after share-all and after accept; lost again after unshare and after revoke (B's view and signal tokens are removed too); pending and declined invites give no access |
| Admin can join, transfer and share any session, and each action is audited | PASS | `test_admin_join_transfer_and_grant_audited`: admin sees all sessions and joins (`session.admin_joined`), transfers (old owner loses access, `session.transferred`), grants a third tech (`session.access_granted`), shares with all (`session.shared_all`); techs cannot transfer or grant; `/admin/sessions` admin-only |
| Invite toast appears on the target tech's dashboard within 2 s | PASS | `tests/e2e/test_phase5_invites.py`: toast under 2 s, Accept opens the session with video, inviter told, customer sees both names, revoke closes the target's view |

Also: "Click to enable sounds" prompt on every portal page (one per page, tested), tab title flashes with the unread count while the tab is hidden, notifications panel keeps a history with action buttons.

Known limitation: revoking access removes the tech's views, signal tokens and closes their viewer, but an already-established peer-to-peer media connection is closed by their own page; a tampered browser could keep it until it drops. Kill ends it for everyone. Techs are trusted staff, so this is acceptable for now.

### Phase 6: Lifecycle, timeouts, audit log (2026-10-01)

| Acceptance check | Result | Evidence |
|---|---|---|
| Integration tests with shortened timeouts cover every `end_reason` | PASS | `tests/integration/test_phase6_lifecycle.py`: `tech_absent_timeout`, `customer_lost_timeout`, `code_expired` through the sweeper with 2 s timeouts; `customer_ended`, `tech_ended`, `tech_killed`, `admin_ended` through their own paths (`test_every_end_reason_reachable`); ending revokes customer and signal tokens |
| A tech closing the portal ends their private session after the timeout. A second tech with access keeps it alive | PASS | `test_tech_absent_timeout`, `test_second_tech_with_access_keeps_it_alive` (a tech WITHOUT access does not keep it alive); browser run with the real worker `tests/e2e/test_phase6_timeouts.py` (countdown shown, then "ended because your technician wasn't connected") |
| CSV export matches filters and opens cleanly in Excel | PASS (structure), PENDING (manual open in Excel) | `test_csv_export_matches_filters_and_is_excel_friendly`: UTF-8 BOM, CRLF line ends, standard quoting, filters by tech, event type and company, non-Latin text kept, formula injection neutralised (`'=...`); `audit.exported` audited |
| Retention job deletes only records older than the threshold | PASS | `test_retention_deletes_only_old_records` (366-day records deleted, 364-day kept, non-ended sessions never deleted) |

Also: absence clocks live in Redis ("absent since"), so a worker restart does not reset them; a customer coming back (for example after the Mac "quit and reopen" step) resets the clock (`test_customer_returning_resets_the_clock`); customer countdown only in the last `tech_absent_warning_seconds`.

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
| D12 | Nightly `pg_dump` via cron in the worker | Separate `backup` service on the `postgres:16.10-alpine` image | Debian bookworm's `pg_dump` is v15 and refuses a v16 server; the postgres image always matches. |
| D13 | Certbot DNS-01 (provider unspecified) | `certbot/dns-cloudflare:v5.8.0` | "DNS only, never proxied" implies Cloudflare. Swap the image and plugin if the zone is elsewhere. coturn restarts on the first certificate and reloads (SIGUSR2) on renewals. |
| D14 | (not specified) | Docker network dynamic addresses limited to `172.31.250.128/25` | Stops dynamic containers taking the router's fixed address. |
| D15 | Data model as listed | Added `users.must_change_password`, `join_codes.id`, `join_codes.created_at`, `join_codes.revoked`, `rustdesk_releases.error`, status `archived` | Needed for admin password resets, code revocation and sync error reporting. |
| D16 | (not specified) | Brand colours are served as `/brand.css` (CSS variables) | Lets the strict CSP forbid inline styles. |
| D17 | Server-side sessions | Also used before login (stage `anon`) to hold the login CSRF token | CSRF on the login and wizard forms. |
| D18 | Wizard "disabled once the first admin exists" | Token step disabled when an admin exists; the remaining wizard steps (branding, URLs, TURN, RustDesk) are open only to that signed-in admin until setup is marked complete | The wizard has to continue after the admin is created. |
| D19 | Publisher reports `audio_available` via the iframe API | Read-only check of the same-origin publisher frame's own local tracks (`session.streamSrc` / `session.screenStream`), with the tech viewer's `new-audio-track-added` as backup | v30.2 has no publisher-side audio event. Nothing in VDO.Ninja is modified or written. |
| D20 | (not specified) | Screen share frames use `audiodevice=0` (no microphone panel or mic mix) and `systemaudio=include` for the first screen; later screens use `ssvo` (video only) | Clean customer UI; audio only from the first stream. |
| D21 | Ending removes the iframe | `{close:true}` is sent first, then the frame is removed | Clean hang-up for the viewer, then every capture track ends. |
| D22 | Framerate path to be decided in Phase 4 | Framerate goes to the customer page, which posts VDO.Ninja's Companion command `{action: "videoConstraint", value: "frameRate", value2: N}` to its own frame | `applyConstraints` on the live track: no reload, no second screen pick. Applies to every viewer of that stream. |
| D23 | Thumbnails pause when hidden | The thumbnail viewer is closed (not just paused) when the tab is hidden, the tile scrolls out of view, or the same tech opens the full view | Zero bytes and zero customer upload for an unseen thumbnail; reconnects in about 1 s. |
| D24 | Balanced: up to 1080p | Viewer asks for at most 1920x1080 (`targetWidth`/`targetHeight`); Clear and Smooth use `scale` 100 and 50 | v30.2 iframe API. |
| D25 | Event list | Added `session.access_granted` (admin gives a tech access) and `session.unshared` | Needed to audit those actions distinctly. |
| D11 | Docker Hub images | Same official images; this build container pulled them via `mirror.gcr.io` because of Docker Hub rate limits | No effect on the LXC. |

## Decisions recorded

| Date | Decision | By |
|---|---|---|
| 2026-10-01 | Accepted the VDO.Ninja same-origin finding with the Phase 0 mitigations (sandboxed tech viewer, framing locked down, top-level navigation refused). A separate VDO hostname is logged as a later hardening step. | Pierce |
| 2026-10-01 | Continue building Phases 1 to 9 in the cloud build container. LXC-only acceptance checks are marked PENDING (LXC) until run on the real LXC. Nothing is made public. | Pierce |

## Open items for Pierce

- **Licence, resale**: `vendor/vdo.ninja/webrtc.js` carries an "All Rights Reserved, cannot be modified without permission" header rather than AGPL. We never modify it, so self-hosting is fine. Before selling installs to other MSPs, confirm with the author or legal that redistributing it unmodified inside a commercial product is acceptable.
- **Security, recommended later**: serve VDO.Ninja from its own hostname (for example `vdo.connect.mhits.au`) to remove the shared-origin risk on the customer side completely. Needs DNS and edge proxy changes (outside this LXC).
