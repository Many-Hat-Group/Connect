// Tech dashboard: new session codes, pending codes, active session tiles
// with low-bitrate thumbnails.
//
// Thumbnails cost the customer upload bandwidth (VDO.Ninja is a mesh), so a
// thumbnail only runs while this tab is visible AND the tile is on screen,
// and it is closed while the same tech has the full view open.
import { api, addVisible, localTimes, notify, onMessage, removeVisible, setVisible, viewClosed, viewOpened } from "./portal.js";
import { Viewer } from "./viewer.js";
import { deviceLabel, duration, el, osIcon } from "./ui.js";

const grid = document.getElementById("sessions");
const pendingList = document.getElementById("pending");
const codePanel = document.getElementById("code-panel");
const empty = document.getElementById("no-sessions");
const banner = document.getElementById("bruteforce-banner");
const tiles = new Map();        // session id -> {el, data, thumbs: Map(stream id -> Viewer|null)}
const fullOpen = new Set();     // stream ids I have open in a full view (any tab)
let state = null;
let refreshTimer = null;
let me = null;

window.__dash = { tiles };      // for tests

// ---------------------------------------------------------------- new session
document.getElementById("new-session").addEventListener("click", async () => {
  try {
    const info = await api("POST", "/api/sessions");
    showCode(info);
    refresh();
  } catch (e) {
    notify({ title: "Couldn't create a session", body: e.message, panel: false });
  }
});

let countdown = null;
function showCode(info) {
  codePanel.hidden = false;
  codePanel.querySelector(".code-big").textContent = info.code;
  codePanel.querySelector(".code-link").textContent = info.link;
  codePanel.dataset.code = info.code;
  codePanel.dataset.link = info.link;
  const expires = new Date(info.expires_at).getTime();
  clearInterval(countdown);
  const tick = () => {
    const left = Math.max(0, Math.round((expires - Date.now()) / 1000));
    codePanel.querySelector(".code-countdown").textContent = left
      ? `Expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : "Expired";
    if (!left) clearInterval(countdown);
  };
  tick();
  countdown = setInterval(tick, 1000);
}

for (const [id, attr, label] of [["copy-code", "code", "Code copied"], ["copy-link", "link", "Link copied"]]) {
  document.getElementById(id).addEventListener("click", async (e) => {
    const text = codePanel.dataset[attr];
    try { await navigator.clipboard.writeText(text); } catch { /* clipboard blocked */ }
    e.target.textContent = label;
    setTimeout(() => { e.target.textContent = attr === "code" ? "Copy code" : "Copy link"; }, 1500);
  });
}
document.getElementById("close-code").addEventListener("click", () => { codePanel.hidden = true; clearInterval(countdown); });

// ---------------------------------------------------------------- data
async function refresh() {
  try {
    state = await api("GET", "/api/dashboard");
  } catch { return; }
  if (!state) return;
  me = state.me;
  banner.hidden = !state.bruteforce;
  if (state.bruteforce) banner.textContent = `Warning: ${state.bruteforce} wrong join codes in the last hour. Check the audit log.`;
  renderPending(state.pending);
  renderSessions(state.sessions);
}

function scheduleRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refresh, 250);
}

onMessage((m) => {
  if (m.type === "session.update" || m.type === "access.revoked") scheduleRefresh();
  if (m.type === "view.full") {
    if (m.open) { fullOpen.add(m.stream_id); closeThumb(m.stream_id); } else { fullOpen.delete(m.stream_id); syncThumbs(); }
  }
});

function renderPending(list) {
  pendingList.replaceChildren(...list.map((p) => {
    const li = el("li", { class: "pending-item" });
    const code = el("strong", { class: "code-small" }, p.code);
    const left = el("span", { class: "muted pending-left", "data-expires": p.expires_at });
    const show = el("button", { class: "btn small", type: "button" }, "Show");
    show.addEventListener("click", () => showCode(p));
    const cancel = el("button", { class: "btn small", type: "button" }, "Cancel");
    cancel.addEventListener("click", async () => { await api("POST", `/api/sessions/${p.session_id}/end`); refresh(); });
    li.append(code, " ", left, " ", show, " ", cancel);
    return li;
  }));
  document.getElementById("pending-wrap").hidden = list.length === 0;
  tickPending();
}

function tickPending() {
  pendingList.querySelectorAll(".pending-left").forEach((s) => {
    const left = Math.max(0, Math.round((new Date(s.dataset.expires).getTime() - Date.now()) / 1000));
    s.textContent = left ? `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} left` : "expired";
  });
}
setInterval(tickPending, 1000);

// ---------------------------------------------------------------- tiles
function renderSessions(list) {
  const ids = new Set(list.map((s) => s.id));
  for (const [id, t] of tiles) {
    if (!ids.has(id)) { destroyTile(t); tiles.delete(id); }
  }
  for (const s of list) {
    let t = tiles.get(s.id);
    if (!t) {
      t = { el: el("article", { class: "tile", "data-session": s.id, tabindex: "0" }), thumbs: new Map(), visible: false };
      t.el.addEventListener("click", (e) => { if (!e.target.closest("button,a")) location.href = `/portal/session/${s.id}`; });
      t.el.addEventListener("keydown", (e) => { if (e.key === "Enter") location.href = `/portal/session/${s.id}`; });
      tiles.set(s.id, t);
      grid.appendChild(t.el);
      observer.observe(t.el);
    }
    t.data = s;
    renderTile(t);
  }
  setVisible([...tiles.keys()]);
  empty.hidden = tiles.size > 0;
  syncThumbs();
}

function renderTile(t) {
  const s = t.data;
  let media = t.el.querySelector(".tile-media");
  if (!media) {
    media = el("div", { class: "tile-media" });
    t.el.prepend(media);
  }
  const info = t.el.querySelector(".tile-info") || t.el.appendChild(el("div", { class: "tile-info" }));
  const streams = s.streams.filter((x) => x.status === "live");
  const status = s.status === "waiting" ? "Waiting for them to share" : s.status === "live" ? "Live" : s.status;
  const viewers = [...new Set(s.streams.flatMap((x) => x.viewers.map((v) => v.name)))];
  info.replaceChildren(
    el("h3", {}, s.customer_name || "Customer"),
    el("p", { class: "tile-company" }, `${s.customer_company || ""}${s.ticket ? ` · Ticket ${s.ticket}` : ""}`),
    el("p", { class: "tile-meta" },
      osIcon(s.os, s.device_class), " ", deviceLabel(s), " ",
      el("span", { class: `pill ${s.status === "live" ? "live" : ""}` }, status), " ",
      s.audio_available === false ? el("span", { class: "pill warn", title: "This device is not sending sound" }, "No audio from this device") : "",
    ),
    el("p", { class: "tile-meta muted" },
      el("span", { class: "duration", "data-start": s.started_at || s.created_at }, duration(s.started_at || s.created_at)),
      ` · ${s.owner_id === me ? "You" : s.owner_name}`,
      s.visibility === "all_techs" ? " · Shared with all techs" : "",
      viewers.length ? ` · Viewing: ${viewers.join(", ")}` : ""),
  );
  // Media placeholders per live stream.
  const want = new Set(streams.map((x) => x.id));
  for (const [sid, v] of t.thumbs) {
    if (!want.has(sid)) { if (v) { v.destroy(); viewClosed(sid); } t.thumbs.delete(sid); media.querySelector(`[data-stream="${sid}"]`)?.remove(); }
  }
  for (const st of streams) {
    if (!t.thumbs.has(st.id)) {
      t.thumbs.set(st.id, null);
      media.appendChild(el("div", { class: "thumb-slot", "data-stream": st.id }, el("span", { class: "thumb-label" }, st.label)));
    }
  }
  if (!streams.length) media.replaceChildren(el("div", { class: "thumb-placeholder" }, s.status === "waiting" ? "Waiting" : ""));
  else media.querySelector(".thumb-placeholder")?.remove();
  media.classList.toggle("multi", streams.length > 1);
}

function destroyTile(t) {
  for (const [sid, v] of t.thumbs) if (v) { v.destroy(); viewClosed(sid); api("POST", `/api/sessions/${t.data.id}/view/close`, { stream_id: sid, kind: "thumb" }).catch(() => {}); }
  observer.unobserve(t.el);
  t.el.remove();
  removeVisible(t.data.id);
}

// ---------------------------------------------------------------- thumbnails
const observer = new IntersectionObserver((entries) => {
  for (const e of entries) {
    const t = tiles.get(e.target.dataset.session);
    if (t) t.visible = e.isIntersecting;
  }
  syncThumbs();
}, { threshold: 0.1 });

document.addEventListener("visibilitychange", syncThumbs);

function syncThumbs() {
  const pageVisible = document.visibilityState === "visible";
  for (const t of tiles.values()) {
    for (const [sid, v] of t.thumbs) {
      const should = pageVisible && t.visible && !fullOpen.has(sid);
      if (should && !v) openThumb(t, sid);
      else if (!should && v) closeThumb(sid);
    }
  }
}

const opening = new Set();
async function openThumb(t, sid) {
  if (opening.has(sid)) return;
  opening.add(sid);
  try {
    const view = await api("POST", `/api/sessions/${t.data.id}/view`, { stream_id: sid, kind: "thumb" });
    const slot = t.el.querySelector(`[data-stream="${sid}"]`);
    if (!slot || !tiles.has(t.data.id) || document.visibilityState !== "visible") {
      await api("POST", `/api/sessions/${t.data.id}/view/close`, { stream_id: sid, kind: "thumb" });
      return;
    }
    const v = new Viewer(slot, view);
    t.thumbs.set(sid, v);
    viewOpened(sid);
  } catch (e) {
    const slot = t.el.querySelector(`[data-stream="${sid}"]`);
    if (slot && e.status === 409) slot.dataset.note = "Viewer limit reached";
  } finally {
    opening.delete(sid);
  }
}

function closeThumb(sid) {
  for (const t of tiles.values()) {
    const v = t.thumbs.get(sid);
    if (v) {
      v.destroy();
      t.thumbs.set(sid, null);
      viewClosed(sid);
      api("POST", `/api/sessions/${t.data.id}/view/close`, { stream_id: sid, kind: "thumb" }).catch(() => {});
    }
  }
}

window.addEventListener("pagehide", () => {
  for (const t of tiles.values()) for (const [sid, v] of t.thumbs) if (v) {
    fetch(`/api/sessions/${t.data.id}/view/close`, { method: "POST", keepalive: true, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": document.body.dataset.csrf },
      body: JSON.stringify({ stream_id: sid, kind: "thumb" }) });
  }
});

setInterval(() => document.querySelectorAll(".duration").forEach((d) => { d.textContent = duration(d.dataset.start); }), 1000);
refresh();
setInterval(refresh, 30000);
localTimes();
