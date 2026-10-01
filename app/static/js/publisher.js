// Wraps one unmodified VDO.Ninja publisher iframe (customer side).
// Behaviour comes only from the server-built URL and the iframe API.
// See docs/vdo-notes.md for the events used.

export class Publisher {
  constructor(container, frame, handlers = {}) {
    this.frame = frame;            // {id, label, share_type, url}
    this.h = handlers;             // onLive(audio), onStopped(), onViewer(n), onAudio(bool)
    this.live = false;
    this.screenOn = false;
    this.viewerCount = 0;
    this.wrap = document.createElement("div");
    this.wrap.className = "frame-wrap";
    const cap = document.createElement("div");
    cap.className = "frame-label";
    cap.textContent = frame.label;
    this.iframe = document.createElement("iframe");
    this.iframe.title = `${frame.label} sharing panel`;
    this.iframe.allow = "camera; microphone; display-capture; autoplay; fullscreen";
    this.iframe.src = frame.url;
    this.wrap.append(cap, this.iframe);
    container.appendChild(this.wrap);
    this._onMessage = (e) => this.onMessage(e);
    window.addEventListener("message", this._onMessage);
  }

  onMessage(e) {
    if (e.source !== this.iframe.contentWindow || e.origin !== location.origin) return;
    const d = e.data;
    if (!d || typeof d !== "object" || !d.action) return;
    switch (d.action) {
      case "screen-share-state":
        // Camera frames also emit screen-share-state false while starting.
        if (this.frame.share_type !== "screen") break;
        if (d.value === true) this.screenOn = true;
        else if (this.screenOn || this.live) this.stopped();   // browser "Stop sharing" bar
        else if (this.h.onCancelled) this.h.onCancelled();       // picker cancelled or denied
        break;
      case "camera-share":
        if (d.value === true) this.screenOn = true;
        break;
      case "seeding":
        if (d.value === true && this.screenOn && !this.live) {
          this.live = true;
          this.h.onLive && this.h.onLive(this.audioPresent());
          // Audio tracks can attach a moment after video.
          setTimeout(() => { if (this.live && this.h.onAudio) this.h.onAudio(this.audioPresent()); }, 2500);
        }
        break;
      case "push-connection":
        this.viewerCount = Math.max(0, this.viewerCount + (d.value ? 1 : -1));
        this.h.onViewer && this.h.onViewer(this.viewerCount);
        break;
      case "hungup":
        if (this.live) this.stopped();
        break;
      default:
        break;
    }
  }

  // Read-only check of the frame's own local capture (same origin). We never
  // write to VDO.Ninja state; if this is unavailable the tech viewer's
  // new-audio-track-added event fills in audio_available instead.
  audioPresent() {
    try {
      const s = this.iframe.contentWindow.session;
      const streams = [s && s.streamSrc, s && s.screenStream].filter(Boolean);
      if (!streams.length) return null;
      return streams.some((st) => st.getAudioTracks().some((t) => t.readyState === "live"));
    } catch {
      return null;
    }
  }

  stopped() {
    if (!this.live && !this.screenOn) return;
    this.live = false;
    this.screenOn = false;
    this.h.onStopped && this.h.onStopped();
  }

  send(msg) {
    try { this.iframe.contentWindow.postMessage(msg, location.origin); } catch { /* frame gone */ }
  }

  // Hang up cleanly, then remove the frame: removing it ends every capture
  // track (verified in Phase 0).
  destroy() {
    this.send({ close: true });
    window.removeEventListener("message", this._onMessage);
    const w = this.wrap;
    setTimeout(() => w.remove(), 150);
    this.iframe.src = "about:blank";
    this.live = false;
  }
}
