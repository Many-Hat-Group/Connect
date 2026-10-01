// Portal shell: websocket, heartbeats, toasts with sound, notifications,
// local time display. Page modules register handlers with onMessage().

const handlers = new Set();
const visibleSessions = new Set();
let ws = null;
let backoff = 1000;
let soundEnabled = false;
let unread = 0;
const baseTitle = document.title;
let titleTimer = null;

export const csrf = document.body.dataset.csrf;

export function onMessage(fn) { handlers.add(fn); return () => handlers.delete(fn); }
export function setVisible(ids) { visibleSessions.clear(); ids.forEach((i) => visibleSessions.add(i)); }
export function addVisible(id) { visibleSessions.add(id); }
export function removeVisible(id) { visibleSessions.delete(id); }

export async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, Accept: "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401) { location.href = "/login"; return null; }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!res.ok) { const e = new Error((data && data.error) || `Request failed (${res.status})`); e.status = res.status; e.data = data; throw e; }
  return data;
}

function sendHeartbeat() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "hb", visible: [...visibleSessions] }));
  }
}

function connect() {
  ws = new WebSocket(`wss://${location.host}/ws/portal`);
  ws.addEventListener("open", () => { backoff = 1000; sendHeartbeat(); });
  ws.addEventListener("message", (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.type === "session.revoked") { location.href = "/login"; return; }
    if (msg.notify) notify(msg.notify);
    handlers.forEach((fn) => { try { fn(msg); } catch (err) { console.error(err); } });
  });
  ws.addEventListener("close", (e) => {
    if (e.code === 4401) { location.href = "/login"; return; }
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 15000);
  });
}

// ---------------------------------------------------------------- sound
let audioCtx = null;
function beep() {
  if (!soundEnabled) return;
  try {
    audioCtx = audioCtx || new AudioContext();
    const o = audioCtx.createOscillator();
    const g = audioCtx.createGain();
    o.type = "sine"; o.frequency.value = 880;
    g.gain.setValueAtTime(0.0001, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.25, audioCtx.currentTime + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.5);
    o.connect(g).connect(audioCtx.destination);
    o.start(); o.stop(audioCtx.currentTime + 0.55);
  } catch { /* ignore */ }
}

function soundPrompt() {
  try { soundEnabled = localStorage.getItem("connect.sound") === "1"; } catch { soundEnabled = false; }
  const b = document.createElement("button");
  b.className = "btn sound-prompt";
  b.type = "button";
  b.textContent = "Click to enable sounds";
  b.addEventListener("click", () => {
    soundEnabled = true;
    try { localStorage.setItem("connect.sound", "1"); } catch { /* ignore */ }
    audioCtx = new AudioContext();
    b.remove();
    beep();
  });
  // Browsers block audio until the user interacts with the page, every load.
  document.body.appendChild(b);
  document.addEventListener("pointerdown", () => {
    if (soundEnabled && !audioCtx) { audioCtx = new AudioContext(); b.remove(); }
  }, { once: true });
}

function flashTitle(text) {
  if (titleTimer || !document.hidden) return;
  let on = false;
  titleTimer = setInterval(() => { document.title = (on = !on) ? `(${unread}) ${text}` : baseTitle; }, 1000);
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && titleTimer) { clearInterval(titleTimer); titleTimer = null; document.title = baseTitle; }
});

// ---------------------------------------------------------------- toasts
export function notify(n) {
  // n: {title, body, actions: [{label, onClick?, href?, primary?}], sticky, panel}
  const box = document.getElementById("toasts");
  if (!box) return;
  const t = document.createElement("div");
  t.className = "toast";
  t.setAttribute("role", "status");
  const h = document.createElement("strong"); h.textContent = n.title; t.appendChild(h);
  if (n.body) { const p = document.createElement("div"); p.textContent = n.body; t.appendChild(p); }
  if (n.actions && n.actions.length) {
    const a = document.createElement("div"); a.className = "toast-actions";
    for (const act of n.actions) {
      const b = document.createElement(act.href ? "a" : "button");
      b.className = "btn small" + (act.primary ? " primary" : "");
      b.textContent = act.label;
      if (act.href) b.href = act.href; else b.type = "button";
      if (act.onClick) b.addEventListener("click", async () => { await act.onClick(); t.remove(); });
      a.appendChild(b);
    }
    t.appendChild(a);
  }
  box.appendChild(t);
  if (!n.sticky) setTimeout(() => t.remove(), 8000);
  if (n.panel !== false) addToPanel(n);
  beep();
  flashTitle(n.title);
}

function addToPanel(n) {
  const list = document.getElementById("notif-list");
  const toggle = document.getElementById("notif-toggle");
  const count = document.getElementById("notif-count");
  if (!list) return;
  const li = document.createElement("li");
  const when = new Date().toLocaleTimeString();
  li.textContent = `${when}  ${n.title}${n.body ? ": " + n.body : ""}`;
  if (n.actions) {
    for (const act of n.actions) {
      if (!act.onClick && !act.href) continue;
      const b = document.createElement(act.href ? "a" : "button");
      b.className = "btn small"; b.textContent = act.label;
      if (act.href) b.href = act.href; else { b.type = "button"; b.addEventListener("click", async () => { await act.onClick(); li.remove(); }); }
      li.appendChild(document.createTextNode(" ")); li.appendChild(b);
    }
  }
  list.prepend(li);
  unread += 1;
  toggle.hidden = false; count.hidden = false; count.textContent = String(unread);
}

function initPanel() {
  const toggle = document.getElementById("notif-toggle");
  const panel = document.getElementById("notif-panel");
  if (!toggle || !panel) return;
  toggle.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    toggle.setAttribute("aria-expanded", String(!panel.hidden));
    if (!panel.hidden) { unread = 0; document.getElementById("notif-count").hidden = true; }
  });
}

export function localTimes(root = document) {
  root.querySelectorAll("time.localtime").forEach((el) => {
    const d = new Date(el.getAttribute("datetime"));
    if (!isNaN(d)) el.textContent = d.toLocaleString();
  });
}

localTimes();
initPanel();
soundPrompt();
connect();
setInterval(sendHeartbeat, 10000);
document.addEventListener("visibilitychange", sendHeartbeat);
