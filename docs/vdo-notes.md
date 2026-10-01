# VDO.Ninja integration notes

| Item | Value |
|---|---|
| VDO.Ninja | `vendor/vdo.ninja`, tag **v30.2**, commit `d5a8b5669d1cb0ff75f984499f7025b745657808` (index.html reports `session.version = "30.1"`) |
| Handshake server | `vendor/websocket_server` (steveseguin/websocket_server), commit `92d9bce4f8c798b520cfd2781d6f7f8ae607b3fb`, file `vdoninja_advanced.js`, AGPL-3.0 |
| Handshake runtime deps | `ws` 8.22.0, `uuid` 14.0.2 (`signal/package.json`, lockfile committed) |
| Verified | 2026-10-01, Chromium 141 (Playwright 1.56), fake media, `tests/e2e/test_phase0_vdo.py` (14 tests passing) |

Rules: never edit anything under `vendor/`. `make check-vendor` enforces it. All behaviour comes from URL parameters and the iframe `postMessage` API.

## How we pass parameters

- **Everything goes in the URL fragment** (`/vdo/#room=...&password=...`). VDO.Ninja merges fragment parameters and they win over the query string (`lib.js` `mergeFragmentParams`). Fragments are never sent to the server, so room, password, TURN credentials and signal tokens stay out of access logs and `Referer`.
- **Percent-encode every value fully.** VDO.Ninja turns every raw `?` in the URL into `&`, and decodes `+` as a space. `tests/e2e/vdo.py:fragment()` and the app's URL builder use `quote(value, safe='')`.
- `/vdo` without a trailing slash makes VDO.Ninja treat `vdo` as a room name. The router redirects `/vdo` to `/vdo/`.

## Confirmed parameters (Section 7 table)

"Test" refers to `tests/e2e/test_phase0_vdo.py`.

| Need | Confirmed parameter or API | Behaviour (verified) | Differs from playbook? |
|---|---|---|---|
| Custom handshake server | **`wss2=connect.mhits.au/signal?t=<token>`** (percent-encoded) | Opens `wss://<value>`. Keeps `customWSS=false`, which is what `vdoninja_advanced.js` expects. The token arrives at our `auth_request` intact (test `test_signal_token_reaches_auth_and_bad_token_is_refused`); a bad token gets 403 and the publisher never seeds. | **Yes**: `wss2`, not `wss`. `wss=` sets `customWSS=true` (stateless broadcast relay mode), which changes message framing. `wss://` is always prepended. |
| Custom TURN | `stun=U;P;turn:H:3478` + `addstun=U;P;turn:H:3478?transport=tcp` + `turn=U;P;turns:H:5349` | `turn=` takes exactly one URL, but all three slots accept credentials, so we get UDP, TCP and TLS entries for our coturn. `stun=` **replaces** the default Google and Cloudflare STUN. Verified: `RTCPeerConnection.getConfiguration().iceServers` contains only our TURN host. | **Yes**: three slots rather than a single `turn=`. |
| No fetch of public TURN list | any `turn=` value | Without `turn=` VDO.Ninja fetches `https://turnservers.vdo.ninja/` (observed, blocked by our CSP). With `turn=` set it is not attempted (zero CSP violations in the tests). | |
| Force relay | `relay` (aliases `privacy`, `private`) | `iceTransportPolicy=relay`; only relay candidate pairs (test `test_relay_only_through_coturn`). Silently caps bitrate at 4000 kbps. | |
| Room and password | `room=`, `password=` | Room on the wire = first 16 hex chars of SHA-256(room + password + salt). Stream ID on the wire = ID + 6 hex chars of SHA-256(password + salt). Salt = `location.hostname`. The handshake server never sees the plain room or stream ID. Room names over 30 chars are truncated before hashing, so we use 30 random alphanumerics plus a 32+ char random password (entropy comes from both). | Room effectively max 30 chars. |
| Publisher stream ID | `push=` | Alphanumeric. Random 24 chars per stream. | |
| Viewer target | `view=` **plus `scene`** | `room` + `view` without `scene` lands on the room lobby ("Join room with camera"). `scene` joins the room as a pure viewer. | **Yes**: `scene` required alongside `view` when a room is used. |
| Screen share publisher | `screenshare` (`ss`) + `autostart` | Starts `getDisplayMedia` at load. Screen audio is requested by default. `systemaudio=include` asks Chrome for system audio. | **Caveat**: real browsers need a user click *inside the iframe* for `getDisplayMedia` (user activation in our page does not pass to the child frame). We therefore do **not** use `autostart` for screen shares in production: VDO.Ninja shows its own "Share screen" button inside the frame. Verify in the device matrix. |
| Camera publisher | `webcam` (`wc`), `videodevice=`, `audiodevice=` | `camera-share` event. `audiodevice=0` disables the mic. | |
| Clean UI | `cleanoutput`, `cleanish`, `nosettings`, `chat=false`, `hidemenu`, `noheader`, `nopreview`/`minipreview` | Hides the matching UI. On our hostname the VDO.Ninja logo, help and report buttons are hidden anyway. | `nochat` and `nologo` do not exist. |
| Label | `label=` | Underscores become spaces. Arrives at the viewer in `view-connection-info.value.label` (test `test_label_reaches_viewer`). An empty `label` opens a prompt, so always give a value. | |
| Viewer quality | viewer-side: `scale=` (percent), `vw=`/`vh=`, `bitrate=` (kbps, SDP munge on the answer). Runtime: `{scale:N}`, `{bitrate:kbps}`, `{targetBitrate}`, `{targetWidth,targetHeight,UUID}` | `{scale:50}` halves received width (test `test_viewer_side_scale_and_bitrate`). | **Framerate is publisher-only** (`maxframerate=`, `fps=`). There is no viewer-side fps request. See "Quality presets" below. |
| Audio | viewer `volume=0` (0 to 100) to start muted; runtime `{volume:0..1}`, `{mute:true/false}` (speaker), `{getDetailedState:true}` | Starts at volume 0; `{volume:0.8}` restores audio (test `test_viewer_starts_muted_and_unmutes_via_api`). Publisher screen audio requested by default. | URL `mute` mutes the **mic**, not the speaker. |
| Events | see "Events we use" below | All confirmed in tests except where noted. | |

## Events we use (iframe to parent `postMessage`)

All events are `{action, value, UUID?, streamID?}`. We pass `iframetarget` (empty value), which makes VDO.Ninja post only to its own origin, and the parent checks `event.source` against the frame's `contentWindow`.

| We need | Event | Side | Verified |
|---|---|---|---|
| Signal connected | `hss-connection` = `connected` | both | yes |
| Stream live (screen) | `screen-share-state` true, then `seeding` true | publisher | yes |
| Stream live (camera) | `camera-share` true, then `seeding` true | publisher | yes |
| User pressed the browser's "Stop sharing" bar | `screen-share-state` false (session stays connected) | publisher | from source; device matrix |
| Share denied or cancelled | `screen-share-state` false without prior true | publisher | from source |
| A viewer connected / left | `push-connection` true / false | publisher | yes |
| Connected to publisher | `view-connection` true, `new-view-connection` | viewer | yes |
| Audio track present | `new-audio-track-added` | viewer | yes (no publisher-side event exists, so the viewer reports it to the app) |
| Publisher gone | `end-view-connection` | viewer | yes (test `test_publisher_close_releases_capture_and_viewer_sees_end`) |
| Our hang-up completed | `hungup` | both | yes (`{close:true}` command) |

Commands we use: `{close:true}` (hang up), `{volume:x}`, `{mute:bool}`, `{scale:N}`, `{bitrate:kbps}` (0 pauses video), `{getStats:true}`, `{getDetailedState:true}`, `{action:"togglescreenshare"}` (there is no dedicated stop-screen-share command).

## Ending a session

Removing the publisher iframe from the DOM ends every capture track (`readyState === "ended"`) and the viewer receives `end-view-connection` (verified). The customer page does this when the app says the session ended. `{close:true}` is sent first for a clean hang-up.

## Quality presets (Phase 4 plan)

| Value | Path |
|---|---|
| Resolution (Clear: native, Balanced: up to 1080p, Smooth: 50 percent) | Viewer-side: `scale` / `{scale:N}` |
| Bitrate | Viewer-side: `bitrate` / `{bitrate:kbps}` |
| Framerate (Clear 5 fps, Balanced 15, Smooth 30) | **Publisher-only.** The app asks the customer page to apply it. The customer page can only do this by reloading the publisher iframe with a new `maxframerate=` (a brief interruption), or through the Companion `videoConstraint` command (to be confirmed in Phase 4). Documented choice goes here once tested. |

Because framerate is publisher-wide, it applies to every viewer of that stream. Thumbnails (`bitrate=150&vw=320&noaudio`) rely on the low bitrate to keep frame rate low (verified: width at most 320, no audio bytes).

## Pausing thumbnails

`{bitrate:0}` pauses inbound video for that viewer, and restoring the thumbnail bitrate resumes it. Phase 4 also removes the thumbnail iframe when the same tech opens the full view.

## Security findings and the mitigations we applied

1. **Iframe API has no origin check and accepts `{function:"eval"}`** (`main.js:8416`, `main.js:9655`). Any page able to frame `/vdo/` could run script on our origin. Mitigations:
   - `/vdo/` sends `Content-Security-Policy: frame-ancestors 'self'` and `X-Frame-Options: SAMEORIGIN` (test `test_vdo_security_headers`).
   - Top-level navigation to `/vdo/` is refused (`Sec-Fetch-Dest: document` returns 403), so a crafted link cannot run VDO.Ninja as a page on our origin (test `test_vdo_refuses_top_level_navigation`).
   - **The tech viewer runs in a sandboxed iframe** (`sandbox="allow-scripts"`, no `allow-same-origin`). VDO.Ninja then runs in an opaque origin and cannot read our cookies, storage or call our APIs, even if a hostile customer found a script-injection bug in VDO.Ninja (test `test_sandboxed_viewer_opaque_origin`). This needs `Access-Control-Allow-Origin: *` on the static `/vdo/` files (public, no credentials).
   - The **customer publisher cannot be sandboxed**: browsers refuse camera and screen capture from an opaque origin (tested: `SecurityError: Invalid security origin`). It runs same-origin; the only party sending it data is our own tech.
   - Recommended later hardening: serve VDO.Ninja from its own hostname (for example `vdo.connect.mhits.au`). That needs DNS and edge proxy changes, so it is not part of this build.
2. **Events posted to `*` by default.** We pass `iframetarget` (empty), which restricts to our own origin. For the sandboxed viewer the origin is opaque, so the parent checks `event.source`.
3. **Public default password** (`someEncryptionKey123`). We always pass a random per-session password.
4. **Shared origin side effects**: VDO.Ninja unregisters all service workers on the origin and writes to `localStorage`. Our app must not rely on a service worker. Never pass `&clear`.
5. **Share links leak the token.** VDO.Ninja builds share links that include `wss2=...?t=<token>` (seen in the `share-link` event). The link is never shown to anyone (clean UI), and signal tokens are short-lived and single-purpose.

## External network requests

Recorded by the tests (`docs/phase0/phase0-screen.netlog.json`, `docs/phase0/phase0-camera.netlog.json`): **every request went to our origin, zero CSP violations.** The `/vdo/` CSP is:

```
default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; style-src 'self' 'unsafe-inline';
img-src 'self' data: blob:; media-src 'self' blob: mediastream:; font-src 'self' data:;
connect-src 'self' wss://<PUBLIC_HOST>/signal; worker-src 'self' blob:; child-src 'self' blob:;
frame-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'self'; form-action 'none'
```

`'unsafe-inline'` and `'unsafe-eval'` are required by VDO.Ninja (inline configuration script in `index.html`, `eval` in the iframe API). This looser CSP applies to `/vdo/` only.

CSP cannot govern ICE (STUN/TURN). That is pinned by `stun=`, `addstun=` and `turn=`. Without them VDO.Ninja would contact `stun.l.google.com`, `stun.cloudflare.com` and `turnservers.vdo.ninja`.

Other hosts in the source (tips, meshcast, tokens, api, Google Drive, Dropbox, YouTube) only fire with their own parameters or user actions we never enable. CSP blocks them anyway.

## Licences

- VDO.Ninja: AGPL-3.0 by default (`LICENCE.md`), `lib.js` and `main.js` AGPL-3.0.
- **`webrtc.js` is not AGPL.** Its header says "All Rights Reserved ... not intended to be modified ... cannot be modified without the express permission of its author." We serve it unmodified, which is compatible with the plan. Flagged to Pierce for the resale question (see AS-BUILT deviations).
- Bundled third party files loaded by `index.html`: `adapter.js` (BSD), `CodecsHandler.js` (MIT), `aes.js` (CryptoJS, BSD).
- Handshake server `vdoninja_advanced.js`: AGPL-3.0.

## Upgrading VDO.Ninja

1. Bump the submodule to the new release tag. Never patch.
2. Re-run `make phase0-up test-phase0`. Every row above must still pass.
3. Re-run the manual device matrix (`docs/device-test-matrix.md`).
4. Update the version table at the top of this file and `docs/AS-BUILT.md`.

## Source research appendix

The line-referenced research behind this file (every parameter, every event and every external host, with `file:line`) is kept in `docs/vdo-source-research.md`. References of the form `w.js:N` point to a decoded, pretty-printed copy of the obfuscated `webrtc.js` produced only for reading (it is not stored in the repo; regenerate by running its own string decoder and a formatter).
