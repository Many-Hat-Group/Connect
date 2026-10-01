"""Helpers for driving unmodified VDO.Ninja in Playwright tests."""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import quote

CHROMIUM_ARGS = [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    "--auto-select-desktop-capture-source=Entire screen",
    "--host-resolver-rules=MAP connect.test 127.0.0.1, MAP turn.test 192.0.2.2",
    "--ignore-certificate-errors",
    "--no-proxy-server",
    "--autoplay-policy=no-user-gesture-required",
]


def fragment(params: dict) -> str:
    """Build a VDO.Ninja fragment. Values are fully percent-encoded because
    VDO.Ninja turns every raw '?' into '&' and decodes '+' as a space."""
    parts = []
    for k, v in params.items():
        parts.append(k if v is None else f"{k}={quote(str(v), safe='')}")
    return "#" + "&".join(parts)


def turn_credentials(secret: str, session_id: str, ttl: int = 4 * 3600) -> tuple[str, str]:
    username = f"{int(time.time()) + ttl}:{session_id}"
    digest = hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
    return username, base64.b64encode(digest).decode()


def ice_params(turn_host: str, user: str, password: str) -> dict:
    """The three ICE slots VDO.Ninja offers: stun=, addstun= and turn=.
    All accept credentials, so all three point at our coturn."""
    return {
        "stun": f"{user};{password};turn:{turn_host}:3478",
        "addstun": f"{user};{password};turn:{turn_host}:3478?transport=tcp",
        "turn": f"{user};{password};turns:{turn_host}:5349",
    }


# JS snippets evaluated in the (same-origin) harness page.
JS_STATS = """async (id) => {
  const s = document.getElementById(id).contentWindow.session;
  const out = {pairs: [], inbound: [], outbound: []};
  const pcs = Object.assign({}, s.rpcs || {}, s.pcs || {});
  for (const k in pcs) {
    const r = await pcs[k].getStats();
    r.forEach(x => {
      if (x.type === 'candidate-pair' && x.nominated && x.state === 'succeeded') {
        const l = r.get(x.localCandidateId), rm = r.get(x.remoteCandidateId);
        out.pairs.push({local: l.candidateType, localAddr: l.address, remote: rm.candidateType, url: l.url || null});
      }
      if (x.type === 'inbound-rtp') out.inbound.push({kind: x.kind, w: x.frameWidth, h: x.frameHeight, fps: x.framesPerSecond, bytes: x.bytesReceived});
      if (x.type === 'outbound-rtp') out.outbound.push({kind: x.kind, w: x.frameWidth, h: x.frameHeight, fps: x.framesPerSecond});
    });
    const cfg = pcs[k].getConfiguration();
    out.iceServers = cfg.iceServers.map(i => i.urls);
    out.iceTransportPolicy = cfg.iceTransportPolicy;
  }
  return out;
}"""

JS_VIDEOS = """(id) => [...document.getElementById(id).contentDocument.querySelectorAll('video')]
  .map(v => ({w: v.videoWidth, h: v.videoHeight, paused: v.paused, muted: v.muted, volume: v.volume,
              tracks: v.srcObject ? v.srcObject.getTracks().map(t => ({kind: t.kind, state: t.readyState})) : []}))"""

JS_LOCAL_TRACKS = """(id) => {
  const s = document.getElementById(id).contentWindow.session;
  const out = [];
  if (s.streamSrc) s.streamSrc.getTracks().forEach(t => out.push({kind: t.kind, state: t.readyState, label: t.label}));
  if (s.screenStream) s.screenStream.getTracks().forEach(t => out.push({kind: t.kind, state: t.readyState, label: t.label}));
  return out;
}"""
