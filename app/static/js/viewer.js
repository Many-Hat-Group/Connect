// Wraps one unmodified VDO.Ninja viewer iframe (tech side).
//
// The frame is sandboxed WITHOUT allow-same-origin: VDO.Ninja runs in an
// opaque origin and cannot reach our cookies, storage or APIs, even if a
// hostile customer found a script-injection bug in it. Messages are matched
// by event.source (the origin is "null"), and commands are posted with "*"
// because an opaque origin cannot be named.

let cibCounter = 0;

export class Viewer {
  constructor(container, view, handlers = {}) {
    this.view = view;               // {url, kind, stream_id, label}
    this.h = handlers;              // onConnected(uuid), onVideo(), onAudioTrack(), onEnded()
    this.uuid = null;
    this.pending = new Map();
    this.wrap = document.createElement("div");
    this.wrap.className = `viewer viewer-${view.kind}`;
    this.iframe = document.createElement("iframe");
    this.iframe.title = `${view.label} from the customer`;
    this.iframe.setAttribute("sandbox", "allow-scripts");
    this.iframe.allow = "autoplay; fullscreen";
    this.iframe.referrerPolicy = "no-referrer";
    this.iframe.src = view.url;
    this.wrap.appendChild(this.iframe);
    container.appendChild(this.wrap);
    this._onMessage = (e) => this.onMessage(e);
    window.addEventListener("message", this._onMessage);
  }

  onMessage(e) {
    if (!this.iframe || e.source !== this.iframe.contentWindow) return;
    const d = e.data;
    if (!d || typeof d !== "object") return;
    if (d.cib && this.pending.has(d.cib)) {
      this.pending.get(d.cib)(d);
      this.pending.delete(d.cib);
      return;
    }
    switch (d.action) {
      case "view-connection":
        if (d.value === true) { this.uuid = d.UUID; this.h.onConnected && this.h.onConnected(d.UUID); }
        break;
      case "new-video-track-added":
        this.videoAt = Date.now();
        this.h.onVideo && this.h.onVideo();
        break;
      case "new-audio-track-added":
        this.audioTrack = true;
        this.h.onAudioTrack && this.h.onAudioTrack();
        break;
      case "end-view-connection":
        this.h.onEnded && this.h.onEnded();
        break;
      default:
        break;
    }
  }

  send(msg) {
    try { this.iframe.contentWindow.postMessage(msg, "*"); } catch { /* gone */ }
  }

  request(msg, timeout = 3000) {
    const cib = `c${++cibCounter}`;
    return new Promise((resolve) => {
      this.pending.set(cib, resolve);
      this.send({ ...msg, cib });
      setTimeout(() => { if (this.pending.has(cib)) { this.pending.delete(cib); resolve(null); } }, timeout);
    });
  }

  stats() { return this.request({ getStats: true }); }
  setVolume(v) { this.send({ volume: v }); }
  setScale(pct) { this.send({ scale: pct }); }
  setBitrate(kbps) { this.send({ bitrate: kbps }); }
  setTargetSize(w, h) { if (this.uuid) this.send({ targetWidth: w, targetHeight: h, UUID: this.uuid }); }

  fullscreen() {
    const el = this.wrap;
    if (document.fullscreenElement) document.exitFullscreen();
    else if (el.requestFullscreen) el.requestFullscreen();
  }

  destroy() {
    window.removeEventListener("message", this._onMessage);
    this.send({ close: true });
    const w = this.wrap;
    if (this.iframe) this.iframe.src = "about:blank";
    this.iframe = null;
    setTimeout(() => w.remove(), 100);
  }
}
