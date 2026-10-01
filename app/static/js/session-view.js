// Session view: full-resolution viewer, quality presets, audio, fullscreen,
// customer details and timeline, End and Kill.
import { addVisible, api, notify, onMessage, viewClosed, viewOpened } from "./portal.js";
import { Viewer } from "./viewer.js";
import { deviceLabel, duration, el } from "./ui.js";
import { initShareMenu } from "./share-menu.js";

const root = document.getElementById("session-view");
const sessionId = root.dataset.session;
const stage = document.getElementById("stage");
const streamTabs = document.getElementById("stream-tabs");
const presetSel = document.getElementById("preset");
const muteBtn = document.getElementById("mute");
const volume = document.getElementById("volume");
const statusLine = document.getElementById("view-status");
let data = null;
let viewer = null;          // the full viewer for the selected stream
let selected = null;        // selected stream id
const side = new Map();     // other streams: thumbnail-quality viewers (Phase 9)
let presets = {};
let muted = true;
let unmutedAudited = false;
let ended = false;

window.__view = { get viewer() { return viewer; }, side, get selected() { return selected; } };  // for tests

addVisible(sessionId);

async function load() {
  try {
    data = await api("GET", `/api/sessions/${sessionId}`);
  } catch (e) {
    if (e.status === 404) { statusLine.textContent = "This session doesn't exist or you no longer have access."; teardown(); }
    return;
  }
  renderInfo();
  if (data.status === "ended") { showEnded(); return; }
  const live = data.streams.filter((s) => s.status === "live");
  renderTabs(live);
  if (!live.length) {
    statusLine.textContent = data.status === "waiting" ? "Waiting for the customer to start sharing." : "No stream yet.";
    if (viewer) closeFull();
    return;
  }
  if (!selected || !live.some((s) => s.id === selected)) select(live[0].id);
  else syncSide(live);
}

function renderTabs(live) {
  streamTabs.hidden = live.length < 2;
  streamTabs.replaceChildren(...live.map((s) => {
    const b = el("button", { type: "button", class: "btn small", "aria-pressed": String(s.id === selected), "data-stream": s.id }, s.label);
    b.addEventListener("click", () => select(s.id));
    return b;
  }));
}

async function select(streamId) {
  if (selected === streamId && viewer) return;
  if (viewer) await closeFull();
  selected = streamId;
  streamTabs.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.stream === streamId)));
  let view;
  try {
    view = await api("POST", `/api/sessions/${sessionId}/view`, { stream_id: streamId, kind: "full" });
  } catch (e) {
    statusLine.textContent = e.message;
    return;
  }
  presets = view.presets;
  statusLine.textContent = "Connecting";
  viewer = new Viewer(stage, view, {
    onConnected: () => { statusLine.textContent = ""; applyPreset(presetSel.value, false); },
    onVideo: () => { statusLine.textContent = ""; },
    onAudioTrack: () => { api("POST", `/api/sessions/${sessionId}/audio`, { stream_id: streamId, detected: true }).catch(() => {}); },
    onEnded: () => { statusLine.textContent = "The stream has stopped."; },
  });
  viewOpened(streamId);
  setMuted(true);
  const live = data.streams.filter((s) => s.status === "live");
  syncSide(live);
}

async function closeFull() {
  if (!viewer) return;
  const sid = selected;
  viewer.destroy();
  viewer = null;
  viewClosed(sid);
  await api("POST", `/api/sessions/${sessionId}/view/close`, { stream_id: sid, kind: "full" }).catch(() => {});
}

// Other streams of this session run at thumbnail quality (Phase 9).
async function syncSide(live) {
  const strip = document.getElementById("side-streams");
  const want = new Set(live.filter((s) => s.id !== selected).map((s) => s.id));
  for (const [sid, v] of side) {
    if (!want.has(sid)) { v.destroy(); side.delete(sid); viewClosed(sid); api("POST", `/api/sessions/${sessionId}/view/close`, { stream_id: sid, kind: "thumb" }).catch(() => {}); }
  }
  for (const sid of want) {
    if (side.has(sid)) continue;
    side.set(sid, null);
    try {
      const view = await api("POST", `/api/sessions/${sessionId}/view`, { stream_id: sid, kind: "thumb" });
      const slot = el("div", { class: "side-slot", "data-stream": sid });
      slot.addEventListener("click", () => select(sid));
      strip.appendChild(slot);
      side.set(sid, new Viewer(slot, view));
      viewOpened(sid);
    } catch { side.delete(sid); }
  }
  strip.hidden = side.size === 0;
}

// ---------------------------------------------------------------- quality
function applyPreset(name, audit = true) {
  const p = presets[name];
  if (!p || !viewer) return;
  viewer.setScale(p.scale);
  if (p.max_height) viewer.setTargetSize(Math.round(p.max_height * 16 / 9), p.max_height);
  viewer.setBitrate(p.bitrate);
  if (audit) api("POST", `/api/sessions/${sessionId}/quality`, { preset: name, stream_id: selected }).catch(() => {});
}
presetSel.addEventListener("change", () => applyPreset(presetSel.value));

// ---------------------------------------------------------------- audio
function setMuted(m) {
  muted = m;
  muteBtn.textContent = m ? "Unmute" : "Mute";
  muteBtn.setAttribute("aria-pressed", String(!m));
  if (viewer) viewer.setVolume(m ? 0 : Number(volume.value) / 100);
}
muteBtn.addEventListener("click", () => {
  setMuted(!muted);
  if (!muted && !unmutedAudited) {
    unmutedAudited = true;
    api("POST", `/api/sessions/${sessionId}/audio`, { unmuted: true, volume: Number(volume.value) }).catch(() => {});
  }
});
volume.addEventListener("input", () => { if (!muted && viewer) viewer.setVolume(Number(volume.value) / 100); });
document.getElementById("fullscreen").addEventListener("click", () => viewer && viewer.fullscreen());

// ---------------------------------------------------------------- end and kill
document.getElementById("end").addEventListener("click", async () => {
  if (!confirm("End this session for the customer?")) return;
  await api("POST", `/api/sessions/${sessionId}/end`);
  load();
});
const killDialog = document.getElementById("kill-dialog");
document.getElementById("kill").addEventListener("click", () => killDialog.showModal());
document.getElementById("kill-cancel").addEventListener("click", () => killDialog.close());
document.getElementById("kill-confirm").addEventListener("click", async () => {
  const block = document.getElementById("kill-block").checked;
  killDialog.close();
  await api("POST", `/api/sessions/${sessionId}/kill`, { block_ip: block });
  load();
});

// ---------------------------------------------------------------- info and timeline
const EVENT_TEXT = {
  "code.issued": "Code issued", "code.claimed": "Code entered", "session.details_submitted": "Details submitted",
  "session.stream_started": "Started sharing", "session.stream_stopped": "Stopped sharing", "session.viewed": "Opened the view",
  "session.view_closed": "Closed the view", "session.audio_unmuted": "Unmuted audio", "session.quality_changed": "Changed quality",
  "session.shared_all": "Shared with all techs", "session.invite_sent": "Invited a tech", "session.invite_accepted": "Accepted an invite",
  "session.invite_declined": "Declined an invite", "session.invite_revoked": "Invite revoked", "session.transferred": "Transferred",
  "session.admin_joined": "Admin joined", "session.rustdesk_prompted": "Asked for remote control",
  "session.rustdesk_downloaded": "Downloaded RustDesk", "session.rustdesk_declined": "Declined remote control",
  "session.ended": "Session ended", "session.ip_blocked": "Blocked the customer's IP for an hour",
};

function renderInfo() {
  document.getElementById("cust-name").textContent = data.customer_name || "Customer";
  const facts = document.getElementById("facts");
  const rows = [
    ["Company", data.customer_company], ["Ticket", data.ticket], ["Device", deviceLabel(data) + (data.os_version ? ` ${data.os_version}` : "")],
    ["Browser version", data.browser_version], ["Audio", data.audio_available === false ? "No audio from this device" : data.audio_available ? "Available" : "Unknown"],
    ["IP address", data.customer_ip], ["Owner", data.owner_name], ["Visibility", data.visibility === "all_techs" ? "All techs" : "Private"],
    ["Status", data.status + (data.end_reason ? ` (${data.end_reason.replaceAll("_", " ")})` : "")],
  ];
  facts.replaceChildren(...rows.filter(([, v]) => v).flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));
  const tl = document.getElementById("timeline");
  tl.replaceChildren(...data.timeline.map((e) => el("li", {},
    el("time", {}, new Date(e.ts).toLocaleTimeString()), " ",
    EVENT_TEXT[e.type] || e.type,
    e.actor ? ` (${e.actor})` : e.actor_type === "customer" ? " (customer)" : "",
    e.type === "session.ended" && e.data && e.data.reason ? `: ${e.data.reason.replaceAll("_", " ")}` : "",
    e.type === "session.quality_changed" && e.data ? `: ${e.data.preset}` : "",
  )));
  const dur = document.getElementById("duration");
  dur.dataset.start = data.started_at || data.created_at;
}
setInterval(() => { const d = document.getElementById("duration"); if (d.dataset.start && !ended) d.textContent = duration(d.dataset.start); }, 1000);

function showEnded() {
  ended = true;
  teardown();
  root.classList.add("ended");
  statusLine.textContent = `Session ended${data && data.end_reason ? ": " + data.end_reason.replaceAll("_", " ") : ""}.`;
  for (const id of ["end", "kill", "preset", "mute", "volume", "fullscreen"]) { const b = document.getElementById(id); if (b) b.disabled = true; }
}

function teardown() {
  if (viewer) closeFull();
  for (const [sid, v] of side) { if (v) v.destroy(); viewClosed(sid); }
  side.clear();
}

let shareMenu = null;
initShareMenu(document.getElementById("share-controls"), sessionId).then((m) => { shareMenu = m; });

onMessage((m) => {
  if (m.type === "session.update" && m.session_id === sessionId) { load(); if (shareMenu) shareMenu.render(); }
  if (m.type === "access.revoked" && m.session_id === sessionId) {
    teardown();
    statusLine.textContent = "You no longer have access to this session.";
    setTimeout(() => { location.href = "/portal"; }, 3000);
  }
});
window.addEventListener("pagehide", () => {
  const ids = [[selected, "full"], ...[...side.keys()].map((s) => [s, "thumb"])];
  for (const [sid, kind] of ids) if (sid) {
    fetch(`/api/sessions/${sessionId}/view/close`, { method: "POST", keepalive: true, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": document.body.dataset.csrf },
      body: JSON.stringify({ stream_id: sid, kind }) });
  }
});

load();
