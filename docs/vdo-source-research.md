# VDO.Ninja v30.x embed research (vendor/vdo.ninja @ d5a8b566, session.version "30.1" in index.html:3260)

Read-only research. Nothing under `vendor/vdo.ninja` was changed.

**About `webrtc.js` citations.** `webrtc.js` is obfuscated and sits on one line (line 9). To read it, I decoded its string table with its own `_0x1832` decoder and ran the result through prettier. The output is at `/tmp/claude-0/s/w.js`, outside the repo. Citations of the form `w.js:N` point to lines in that file. To find the same code in the real file, search it for the quoted identifiers.

**How parameters are parsed.** In `lib.js:393-396`, every `?` in `location.search` is turned into `&`. Then `mergeFragmentParams` (`lib.js:368-391`) merges in the `#fragment` parameters, and the fragment wins when a key appears in both. Values come from `URLSearchParams.get()`, which decodes them once. Two consequences:

- Any literal `?` inside a value (TURN `?transport=tcp`, a WSS `?t=`) must be written as `%3F`.
- Any `+` must be written as `%2B`, because `+` is decoded as a space. This matters for base64 TURN REST credentials.

---

## SECURITY ALERTS (read before deploying anything internet-facing)

1. **The iframe API has no origin check and can run arbitrary code.**
   - `main.js:9655` sets `window.onmessage = session.remoteInterfaceAPI` whenever the page is inside an iframe.
   - `remoteInterfaceAPI` (`main.js:8388-9651`) never checks `e.origin` or `e.source`.
   - It accepts `{function:"eval", value:"..."}` (`main.js:8416-8417`) and `{style:"..."}` (CSS injection, `main.js:9264`).
   - Because VDO is served from **our origin** (`/vdo/`), any third-party site that can iframe `/vdo/` can run JavaScript on our origin. That gives it our localStorage, our non-HttpOnly cookies, and credentialed same-origin requests.
   - **Fix:**
     - Send `Content-Security-Policy: frame-ancestors 'self'` and `X-Frame-Options: SAMEORIGIN` on every `/vdo/*` response. This is mandatory.
     - Better: serve VDO from a separate origin, such as a dedicated subdomain with no shared cookies.
     - Also run a strict CSP `connect-src`, `script-src` and `img-src` allow-list (see the external hosts section).
2. **Events go to any parent by default.** `session.iframetarget` defaults to `"*"` (`w.js:2183`), so every event is posted to whichever page embeds VDO. Add `&iframetarget` with an empty value, which resolves to `window.location.origin` (`main.js:6775-6781`), or pass our origin explicitly. Combine this with `frame-ancestors`.
3. **The default password is public.** `index.html:3263` sets `session.defaultPassword = "someEncryptionKey123"`, and that file is in the public repo, which we cannot edit. Without `&password=`, room and stream-ID hashing uses this known key, so it only obscures them. Pass a random per-session `&password=` and put it in the `#fragment` so it stays out of server logs and Referer.
4. **Sharing our origin has side effects.**
   - `main.js:10284-10298` unregisters **every** service worker registered on the origin, except scopes containing `/notifications/`. If our app has a service worker, VDO will kill it.
   - `&clear` / `&clearstorage` calls `localStorage.clear()` on the whole origin (`lib.js:5040`, `main.js:5797`).
   - VDO writes keys such as `turnlist`, `permaid` and `settings` into our localStorage.
   - A separate origin fixes all three.
5. **TURN credentials are visible to the browser user**, which is unavoidable. Use coturn `use-auth-secret` short-lived REST credentials, put them in the fragment, and percent-encode `+`, `/` and `=`.
6. **A remote peer can make our page fetch from a host it chooses.** If a peer advertises `acceptsTips`, `tipId` and `tipServer`, our page fetches `tipServer/v1/performer/<id>` (`lib.js:23047-23061`). CSP `connect-src` blocks this, and blocking it does no harm.

---

## Main table

| # | Need | Confirmed param / API | Behaviour (file:line) | Caveats |
|---|---|---|---|---|
| 1 | Custom handshake server | **`wss2=host[/path]`**. Do not use `wss=`. | `main.js:7434-7441`: `wss2` sets `session.wss` and `wssSetViaUrl=true` and leaves `customWSS=false`. `wss=` (`main.js:7425-7433`) also sets `customWSS=true`. If the value does not start with `wss://`, `wss://` is prepended. The socket is opened in `session.connect` with `new WebSocket(session.wss)` (`w.js:6242`). The default when unset is `wss://wss.vdo.ninja:443`, or `wss://proxywss.rtc.ninja:443` if `&proxy` (`w.js:6211-6214`). | **With `customWSS=true` the client acts as if the server is a dumb broadcast relay:** it adds `msg.from=UUID` and `roomid` to every message (`w.js:5505-5514`), drops incoming messages without a matching `UUID` or `roomid` (`w.js:6391-6420`), puts `streamID` in `joinroom` (`w.js:5416-5418`), skips `ping` keepalive (`w.js:5360`) and skips `wssid` reconnect cleanup (`w.js:6222`). That is wrong for `vdoninja_advanced.js`, so use `wss2`. **Token:** `wss2=myhost%2Fws%3Ft%3DTOKEN` decodes once to `myhost/ws?t=TOKEN` and reaches `new WebSocket` intact. It must be percent-encoded, because a raw `?` becomes `&` (`lib.js:394`). `ws://` is impossible because the `wss://` prefix is forced. `wss` takes precedence over `wss2`, and `pie`, `audience` and `bypass` also alter this. Generated share links append `&wss2=`+session.wss unencoded (`lib.js:24942-24948`), so the token would appear in any VDO-generated link. |
| 2 | TURN / STUN | `turn=user;pass;url` (exactly one URL), `stun=url` or `stun=user;pass;url`, `addstun=url` or `addstun=user;pass;url`, `turn=false`/`off`/`0` (STUN only), `turn=nostun` (no ICE servers), `tcp` | `main.js:6800-6815`: `stun` **replaces** `session.stunServers`; `stun=false` sets it to `[]`. `main.js:6817-6829`: `addstun` concatenates one more entry. `main.js:6840-6929`: `turn` splits on `;`. Three parts become `{username, credential, urls:[part3]}` and are pushed onto `session.stunServers`, which is the same array as `configuration.iceServers`. **Any non-twilio `turn=` sets `session.configuration`.** The defaults are `stun:stun.l.google.com:19302` and `stun:stun.cloudflare.com:3478` (`index.html:3265`, `w.js:1922`). | **The remote TURN list is fetched only when `session.configuration` is falsy:** `chooseBestTURN()` returns early if it is set (`w.js:524-533`). It is called from `connect()` (`w.js:6221`) and before each RTCPeerConnection (`w.js:8928, 18134, 25357`). `getTURNList()` (`w.js:379-522`) fetches `https://turnservers.vdo.ninja/?ts=...`, or uses the localStorage `turnlist` cache. If the fetch fails it falls back to hard-coded vdo.ninja TURN servers. **So `&turn=...` (or `turn=false`) stops the fetch. `stun=` alone does not.** **Multiple TURN URLs:** comma-separated values do not work, because the value goes into `urls:[...]` as one string, and there is no `addturn`. Use the three slots instead (example below). Alternative: the parent page is same-origin, so it can set `iframe.contentWindow.session.configuration = {iceServers:[...], sdpSemantics: ...}` before the first connect. That has a timing race, so prefer URL params. `tcp` only filters the fetched list (`w.js:425`). TURN entries also act as STUN, so dropping the Google default is fine. |
| 3 | Force relay | `relay` (aliases: `privacy`, `private`) | `main.js:6941-6990`: sets `session.privacy` and `configuration.iceTransportPolicy="relay"` if a configuration already exists. Put `turn=` in the same URL; it is parsed earlier (6840). `getTURNList` also applies relay (`w.js:410, 511`). | Silently caps `maxvideobitrate` and `bitrate` at **4000 kbps** (6000 kbps with `speedtest`) (`main.js:6966-6990`). A string value such as `relay=abc` is sent as `&code=abc` to turnservers.vdo.ninja, but only if that fetch happens. Even without `relay`, `escalatePeerToRelay` can switch individual peers to relay once there are 5 or more peers and TURN is configured (`w.js:8176-8184`, called from `w.js:8504, 8561`). |
| 4 | Room / password / hash | `room` / `roomid` / `r`; `password` / `pass` / `pw` / `p`; `password=false` (or `0`/`off`) or `nopassword`; `hash` / `crc` / `check`; `salt` | `main.js:7172-7180` reads the room (sanitized). `main.js:3459-3497` reads the password (`decodeURIComponent` then `sanitizePassword`). If none is given, the default password is used (`main.js:3560-3562`). `generateHash` = SHA-256 hex truncated to N hex chars (`w.js:316-333`). **Stream ID on the wire** = `streamID[0:64] + SHA256(password+salt)[0:6]` (`w.js:5517-5531`). Incoming IDs have the suffix removed by `desaltStreamID` (`w.js:5335`). **Room on the wire** = `SHA256(room+password+salt+token)[0:16]` (`w.js:5421-5455`). With no password the room is sent **in plaintext** (`w.js:5470-5486`). `salt` defaults to `location.hostname` on our domain, or `"vdo.ninja"` for localhost, IP addresses and *.vdo.ninja (`w.js:2472-2495`). | `hash=` is only a check on the password (`main.js:3504-3556`). A mismatch sets `taintedSession`, which blocks connecting. It does not replace the password. Publisher and viewer must share the same password **and** hostname/salt, or they will not find each other. An empty `&password` (no value) opens a prompt. |
| 5 | Publisher | `push` / `id` / `permaid` (`main.js:7075-7110`, sanitized, URL rewritten); `screenshare` / `ss` (`main.js:2058-2062`); `autostart` / `autojoin` / `aj` / `as` (`main.js:4931`); `webcam` / `wc` (`main.js:2037`); `videodevice` / `vd` / `vdevice` / `device` / `d` (`main.js:4797`); `audiodevice` / `ad` / `adevice` (`main.js:4842`); `sstype` / `screensharetype` (`main.js:2136`) | `ss`+`autostart` pushes `publishScreen` into the startup functions (`main.js:4944-4947`). `publishScreen2` calls `getDisplayMedia` (`lib.js:40324`) and requests **audio by default**: the constraints have an `audio:{}` object (`lib.js:36371-36373`), and if that throws it retries without audio (`lib.js:40733-40752`). Audio-related flags: `systemaudio=include` (Chrome `systemAudio`, `main.js:2173`), `screensharevideoonly` / `ssvo` (no audio, `main.js:7683`), `displaysurface=browser\|window\|monitor`, `prefercurrenttab`, `selfbrowsersurface`, `surfaceswitching` (`main.js:2163-2180`), `ssfps` / `screensharefps`. Mic off: `audiodevice=0` (or `false`/`off`). Camera off: `videodevice=0`. | `noaudio` is a **viewer playback** filter that asks publishers for no audio (`main.js:5046`, `w.js:19040-19045`). It does not turn off the publisher's mic. `mute` / `muted` / `m` starts the **mic muted** (`main.js:2229`). `getDisplayMedia` without a user gesture may be rejected; user activation in the parent does not reach a child frame. Expect the user to click inside the iframe. The iframe needs `allow="camera; microphone; display-capture; autoplay; fullscreen"`. The exact `audiodevice` / `videodevice` match uses `normalizeDeviceLabel`. |
| 6 | Viewer | `view` / `v` / `streamid` / `pull` (comma-separated list, `main.js:4183`); `scene` / `scn` (`main.js:2822`); `solo` (forces `scene=0` if unset, `main.js:2873`) | Viewer-side (sent as a request to the publisher): `scale=N` (% of source; disables dynamic scaling, `main.js:5602-5612`, sent as `settings.scale`, `w.js:19240`); `viewwidth` / `vw`, `viewheight` / `vh` (sent as `requestResolution`, `w.js:19242-19253`); `bitrate` / `videobitrate` / `vb` kbps (viewer **munges the answer SDP**, `w.js:13626-13631`); `codec`; `audiobitrate`; `noaudio` / `novideo`. **Publisher-only** (capture/encode): `maxvideobitrate` / `mvb` cap (`main.js:5818`, `w.js:4824`); `width` / `w`, `height` / `h`, `quality` / `q`; `fps` / `fr` / `framerate` and `maxframerate` / `mfr` / `mfps` (only used in getUserMedia/getDisplayMedia constraints, e.g. `lib.js:36432-36443`). | Without `scale` or `vw` the viewer scales **dynamically**: it requests a resolution matching the video element size (`lib.js:9287-9291`). **There is no viewer-side framerate request**; the list of publisher-handled request keys (see below) has no fps key. Runtime iframe API: `{bitrate:kbps, target?/UUID?/streamID?, lock?}` (`main.js:8844`), `{targetBitrate, targetAudioBitrate}` (`main.js:8773`), `{manualBitrate}` (SDP renegotiation, `main.js:8814`), `{audiobitrate}` (**bug: reads `e.data.bitrate`**, `main.js:8877-8905`), `{scale:N\|false, UUID?/target?}` (`main.js:9559`; the UUID branch uses an undefined var and goes to all peers), `{targetWidth, targetHeight, UUID}` (`main.js:9543-9557`). `{bitrate:0}` pauses video. |
| 7 | Clean UI | `cleanoutput` / `clean` (`main.js:602`); `cleanish` (also sets `cleanOutput`, `main.js:602, 6206`); `cleanviewer` / `cv`; `nosettings` / `ns` (`main.js:1002, 2765`); `chat=false` / `chatbutton=false` (`main.js:2381`); `hidehome` (`main.js:624`); `hidemenu` / `hm` (`main.js:8077`); `hideheader` / `noheader` / `hh` (`main.js:6154`); `nopreview` / `np` (`main.js:4306`); `minipreview` / `mini` (`main.js:4313`); `nomicbutton` / `nmb` | Each one hides the matching DOM element. `cleanOutput` also stops `warnUser` popups in many places. | **`nochat` and `nologo` do not exist.** On a non-vdo.ninja hostname the logo, help and report buttons are already hidden (`main.js:521-584`). On iOS, `nopreview` becomes `minipreview=3`. |
| 8 | Labels | `label` / `l` (`main.js:3589-3615`) | `decodeURIComponent`, `_` becomes a space, `sanitizeLabel`. Sets `document.title` and is sent to peers in `info.label` (`w.js:19145`). | An empty `&label` opens a prompt. `showlabels` / `sl` is a separate setting that shows overlay labels. |
| 9 | Audio | URL: `volume` / `vol` = 0-100, the default for new playback elements (`main.js:4449`); `speakermute` / `mutespeaker` / `sm` / `ms` (`main.js:2350`); `noaudio` / `na` (do not receive audio); `novideo` / `nv`; `deafen` (director-only) | iframe: `{mute:true\|false\|"toggle"}` = **speaker/playback** mute (`main.js:8670`), `{speaker:...}` (the inverse, 8683), `{volume:0..1, target?}` (`main.js:8723`; values > 1 are divided by 100), `{mic:true\|false\|"toggle"}` (`main.js:8478`), `{camera:...}` (8508), `{getLoudness:true}` (snapshot plus pushed `action:"loudness"` messages, `main.js:9137`, `lib.js:55750-55765`) | **`getAudioState` does not exist.** Use `{getDetailedState:true}` (returns `muted`, `speakerMuted`, and per-stream `videoVolume` / `muted`, `lib.js:21231-21450`), or the `audio-mute-state` / `mic-mute-state` events. URL `mute` is the **mic**, not the speaker. |
| 10 | Events (to parent) | `pokeIframeAPI(action,value,UUID,SID)` posts `{action, value, UUID, streamID}` (`lib.js:10799-10840`) | See the event list below. | `targetOrigin` is `session.iframetarget`, which defaults to `"*"`. |
| 11 | Commands | `{close:true}` / `{hangup:true}`, also `"estop"` / `"reload"` (`main.js:9244-9257`) leads to `session.hangup` and then the `hungup` event; `{action:"togglescreenshare"}` (Companion command, `lib.js:63478`, via `processMessage` at `main.js:9593`, `lib.js:64173`); `{action:"hangup"}`; `{getStats:true, streamID?, cib?}` returns `{stats, cib}` (`main.js:9101`, `getQuickStats` `lib.js:21197`); `{getFreshStats:true}` (`main.js:9011`); `{getDetailedState:true}`; `{getStreamIDs:true}`; `{getStreamInfo:true}`; `{getRemoteStats}` / `{requestStatsContinuous}`; `{function:"publishScreen"}` | Companion `commands`: raisehand, togglehand, togglescreenshare, chat, speaker, mic, camera, video, hangup, bitrate, requestStats, getDetails, getStats, getGuestList, reload, volume, forceKeyframe, panning, record, group…, layout, width, height, aspectRatio, videoConstraint (`lib.js:63468-64160`). URL `stats` / `nostats` toggles the stats menu (`main.js:6094`). | Any posted object containing `action` is treated as a command. There is **no dedicated "stop screenshare" command**; `togglescreenshare` flips the state. `getQuickStats` references the undefined `myStats` when WHIP or WHEP is active, and the error is swallowed. |
| 12 | External requests | see the external hosts section | | |
| 13 | Subpath `/vdo/` | Works with a **trailing slash**. | `index.html` uses `./` for every script, CSS and favicon (`index.html:51-94, 3387, 3434-3436`). `manifest.json` uses `start_url` / `scope "./"`. Translations load from `./translations/<lang>.json` (`lib.js:10134`). | `/vdo` without a slash makes `filename="vdo"`, which becomes **the room name** (`main.js:1639-1675`), and breaks the relative paths. Redirect `/vdo` to `/vdo/`. Absolute paths: `<link rel=author href=/about>` and `rel=alternate href=/rawdoc.md` (`index.html:38, 59`, never fetched), an `<a href=/rawdoc.md>` (3231), and the notification icon `/media/old_icon.png` (`lib.js:33954`, a harmless 404). |
| 14 | Thumbnail viewer (~320 px wide, 2-5 fps, ~150 kbps) | `&view=ID&bitrate=150&vw=320` (or `&scale=17` for 1920 px sources) `&noaudio` `&cleanoutput`; at runtime `{bitrate:150}` / `{targetWidth:320, UUID}` | `bitrate` caps the publisher's encoder for this viewer only, through answer SDP. `vw` / `scale` / dynamic scaling request a smaller encode. `noaudio` asks the publisher not to send audio. | **The 2-5 fps target can only be set publisher-wide** (`maxframerate=5`), which affects every viewer. At ~150 kbps the encoder drops frames on its own. You can still set `maxvideobitrate` on the publisher. |
| 15 | Stay P2P | The default is P2P. Viewer: `nomeshcast` / `nowhep` sends `allowmeshcast=false` (`main.js:1571`, `w.js:19079`). | Meshcast or WHEP is only used with `meshcast` / `meshcast2` (`main.js:1558-1563`), `whepshare` / `whepsrc` (`main.js:7759`), or WHIP-out parameters. | Nothing switches to meshcast automatically. |
| 16 | Licences | | `webrtc.js:1-8`: "Copyright (c) 2026 Steve Seguin. All Rights Reserved. … yet is not intended to be modified. This file cannot be modified without the express permission of its author." This is **not AGPL**. `lib.js:1-8` and `main.js:1-8`: AGPLv3 ("APGLv3" typo). `electron.html:346`: AGPLv3. `LICENCE.md`: AGPL-3.0 by default unless a file's header says otherwise. | Third-party files loaded by index.html: `thirdparty/adapter.js` (BSD, WebRTC project), `thirdparty/CodecsHandler.js` (MIT, Muaz Khan), `thirdparty/aes.js` (CryptoJS 3.1.2, BSD). Other thirdparty files: StreamSaver/sw.js (MIT, Jimmy Wärting), polyfill / qrcode / webmidi (MIT), jquery (MIT), ffmpeg.min.js, aframe, focus_worker / measureBlur (Timo Taglieber). `auth-client.js`, `podcast/*` and `core/*` have no header, so they are AGPL. |

### Example: three TURN/STUN entries (all values percent-encoded)
```
#stun=U;P;turn:turn.example.com:3478
 &addstun=U;P;turn:turn.example.com:3478%3Ftransport%3Dtcp
 &turn=U;P;turns:turn.example.com:5349
 &relay&wss2=hs.example.com%2Fws%3Ft%3DTOKEN&password=RANDOM&iframetarget
```
- Order matters. `stun` is parsed at line 6800, `addstun` at 6817 and `turn` at 6840, and `turn` pushes onto the same array.
- The result is 3 iceServers, with no Google or Cloudflare STUN and no fetch to turnservers.vdo.ninja.
- U and P must not contain `;`. Encode `+`, `/`, `=` and `&`.

---

## Events emitted to the parent

`{action, value, UUID?, streamID?}` sent through `pokeIframeAPI` (`lib.js:10799`).

Naming: `pcs` are *outbound* peers (someone watching me). `rpcs` are *inbound* peers (a stream I am watching).

**Publisher side**

| Event | When | Where |
|---|---|---|
| `screen-share-state` true | Screen capture started | `lib.js:40361, 40692` (publishScreen2); toggleScreenShare 35245/35279 |
| `screen-share-state` false | Browser "Stop sharing" bar. `track.onended` calls `toggleScreenShare()` and the event fires. The session **stays seeded**; it does not hang up. | `lib.js:40365-40367` leading to `lib.js:35284-35286` |
| `screen-share-state` false | getDisplayMedia was denied or cancelled | `lib.js:40723-40724` |
| `camera-share` true, `started-camera` (deprecated) | Webcam published | `lib.js:39888-39889` |
| `seeding-started`, `seeding` true | Stream registered with the handshake server (`request:"seed"`) | `w.js:3313-3314` |
| `seeding` false, `screen-share-state` false, `director-share` false | Hangup | `w.js:7795-7807` |
| `push-video-track` (value = track id) | Outbound video track replaced | `lib.js:38083` |
| `push-connection` true, `new-push-connection` true | **A viewer connected** (datachannel open) | `w.js:9249-9250` |
| `new-push-connection` false | Send channel closed | `w.js:9255` |
| `push-connection` false | **A viewer disconnected** (closePC) | `w.js:8089` |
| `push-connection-info` | The viewer's info payload | `w.js:9867` |
| `mic-mute-state`, `video-mute-state` | Local mute state changed | `lib.js:18233, 19688` |
| `local-microphone-event`, `local-camera-event` | Local mic or camera event | `lib.js:34032, 37835` |
| `hungup` true | Our own hangup completed | `lib.js:20623` |

Audio track present on the publisher: there is no local event. Options:
- Check that `getDisplayMedia` audio was granted by using viewer-side `new-audio-track-added`.
- Poll `{getStats:true}`, whose result includes `stats.outbound[UUID]`.

**Viewer side**

| Event | When | Where |
|---|---|---|
| `requested-stream` (value = streamID) | Play request sent | `w.js:5387` |
| `stream-id-detected` | Stream ID seen | `w.js:5351-5356` |
| `new-view-connection` true, `view-connection` true | Connected to a publisher | `w.js:19275-19276` |
| `video-element-created` (value `"videosource_"+UUID`) | Video element created for the inbound stream | `lib.js:6766` |
| `new-stream-added`, `new-video-track-added`, `new-audio-track-added` (with UUID and streamID) | **The remote stream has audio or video tracks** | `lib.js:6691-6699` |
| `view-connection` false, `new-view-connection` false, `end-view-connection` | Publisher gone | `w.js:8736, 8814-8817` |
| `view-stats-updated` | Inbound stats updated | `lib.js:16050` |
| `audio-mute-state` | Speaker mute toggled | `lib.js:18353` |

**Both sides**

| Event | Where |
|---|---|
| `hss-connection` / `hssConnection` = connected / reconnected / timeout (30 s) / closed | `w.js:6249, 6304, 6346, 7324` |
| `joining-room` | `w.js:5450, 5487` |
| `joined-room-complete` | `lib.js:27391` |
| `approved` / `rejected` | `w.js:11750-11755, 19478-19510` |
| `guest-connected` | `w.js:11921` |
| `reloading` | `lib.js:20639` |
| `transfer` / `transferred` | |
| `iframe-loaded` | `lib.js:42473` |
| `tip` | `lib.js:23553` |
| `loudness` | `lib.js:55765` |

Other `parent.postMessage` payloads that have no `action` key:

| Payload | Where |
|---|---|
| `{chat}` / `{gotChat, chat}` | `lib.js:50604, 51081` |
| `{dataReceived, UUID}` | `w.js:3239` |
| `{sensors}` | `w.js:20033` |
| `{remoteStats, streamID, UUID}` | `lib.js:15386` |
| `{effectsData}` | `lib.js:14204` |
| `{type:"frame", frame}` (`sendframes`) | `lib.js:55198, 55231`; `main.js:8628` |
| `{bypass}` (`&bypass` mode) | `w.js:6237` |
| Command responses `{stats}`, `{detailedState}`, `{guestList}`, `{streamIDs}`, `{streamInfo}` with `cib` | `main.js:9091-9289` |

Some names from the brief do not exist in this version: `director-connected`, `getAudioState`, `changeVideoBitrate`, `nochat`, `nologo` and `screenshareaudio`.

**Request keys the publisher handles from viewers** (`w.js`, `"x" in msg`): bitrate, targetBitrate, audioBitrate, targetAudioBitrate, optimizedBitrate, scale, requestResolution, keyframe, audio, video, … Neither list contains a framerate key.

---

## External hosts (default config on our own hostname)

| Host | Trigger | Prevent / CSP impact |
|---|---|---|
| `wss://wss.vdo.ninja:443` (or `proxywss.rtc.ninja`) | Always, unless `session.wss` is set (`w.js:6211-6214`) | Pass `wss2=`. Blocking it would break signalling, so it must be overridden. |
| `https://turnservers.vdo.ninja/?ts=` (also `.rtc.ninja` / `.socialstream.ninja` by hostname) | Whenever `session.configuration` is unset (`w.js:379-533`) | Pass any `turn=` (including `turn=false`). If blocked, the 2 s timeout leads to the hard-coded vdo.ninja TURN fallback (`w.js:446-497`). The localStorage `turnlist` cache is also honoured. |
| Fallback TURN `turn-*.vdo.ninja`, `turn.obs.ninja` (UDP/TCP/TLS, not HTTP) | Only when the fetch fails and no `turn=` was given | Avoided by `turn=`. CSP cannot block ICE. |
| `stun.l.google.com:19302`, `stun.cloudflare.com:3478` | Default `stunServers` (`index.html:3265`) | Pass `stun=` (our server) or `stun=false`. CSP cannot block ICE. |
| `qos.vdo.ninja/v1/report` (sendBeacon) | Only when `location.hostname` is `vdo.ninja` or `dev.versus.cam` (`w.js:2497-2503`, `lib.js:20337`) | Not triggered on our host. |
| `reports.vdo.ninja` | Error-report confirm dialog. `errorReport=false` on non-official hosts (`main.js:521-522`, `lib.js:640-653`) | Not triggered. |
| `debug.vdo.ninja` websocket | Remote debug, refused off official hosts (`w.js:150-155`) | Not triggered. |
| `tokens.vdo.ninja` | Only with `&token` (`lib.js:27820-27893`) | Do not use `token`. |
| `vdo-ninja-auth-service.vdo.workers.dev` | Only with `&auth` / `requireauth` / `authtoken` / `universaltoken` (`auth-client.js:4-33`) | Do not use those parameters. |
| `ninjabacker.com`, `tip.vdo.ninja`, `js.stripe.com` | Tips, only if enabled or advertised by a peer (`lib.js:23014-23702`; `initTipNotifications` needs `session.receiveTips`, `lib.js:23560`) | Block with CSP; harmless. |
| `meshcast.io` / `app.meshcast.io` / `qos.meshcast.io` | Only with `meshcast` / `meshcast2` / whip params | Block; harmless. |
| `api.vdo.ninja` (wss) | Only with `&api` / `&osc` | Do not use. |
| `whip.vdo.ninja`, `pipe.vdo.ninja`, `call.vdo.ninja`, `audience.vdo.ninja`, `cloudflare.vdo.ninja`, `g.webrtc.live-video.net`, `temp.vdo.ninja` (`postimage`), `notify.vdo.ninja` (`notify` / `poke`), `free3.piesocket.com` (`pie`), `china.rtc.ninja` (`china.` / `alt.` hosts) | Only with their own parameters | Harmless to block. |
| `googleapis.com`, `apis.google.com`, `accounts.google.com`, `api.dropboxapi.com`, `cdnjs.cloudflare.com` (Dropbox SDK), `www.youtube.com`, `player.twitch.tv`, `player.vimeo.com` | Only from user actions (Drive/Dropbox recording, YouTube chat, `&website=`) | Harmless to block. |
| `updates.vdo.ninja`, `docs.vdo.ninja`, `discord.vdo.ninja`, `github.com`, `whatismyipaddress.com` | Plain `<a href>` links only | No requests are made. |
| Fonts, translations, icons | All local (`lineawesome/`, `./translations/`, `./media/`) | No external font hosts. |

Suggested CSP for `/vdo/`:
- `default-src 'self'`
- `connect-src 'self' wss://<our-handshake-host>`
- `script-src 'self' 'unsafe-inline' 'unsafe-eval'` (inline config scripts in index.html, and `eval` in the iframe API)
- `img-src 'self' data: blob:`
- `media-src 'self' blob:`
- `frame-ancestors 'self'`

ICE traffic to TURN/STUN is not covered by CSP. It is controlled only by `stun=` and `turn=`.
