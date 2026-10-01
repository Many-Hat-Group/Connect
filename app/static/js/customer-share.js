// Customer share page: talks to /ws/customer, manages publisher frames,
// shows who is viewing, instructions, warnings and the end of the session.
import { Publisher } from "./publisher.js";

const data = JSON.parse(document.getElementById("page-data").textContent);
const framesEl = document.getElementById("frames");
const statusText = document.getElementById("status-text");
const viewersText = document.getElementById("viewers-text");
const warningEl = document.getElementById("warning");
const btnAgain = document.getElementById("share-again");
const btnAdd = document.getElementById("add-screen");
const btnStop = document.getElementById("stop-sharing");
const endForm = document.getElementById("end-form");
const canScreen = !!(navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia);
const publishers = new Map();   // frame id -> Publisher
let ws = null;
let ended = false;
let backoff = 1000;
let phase = "instructions";

window.__connect = { publishers, data };  // for tests and support

// ---------------------------------------------------------------- device and instructions
function refinedDevice() {
  let os = data.device.os;
  let browser = data.device.browser;
  if (os === "macos" && navigator.maxTouchPoints > 1) os = "ios";  // iPadOS reports as a Mac
  return { os, browser };
}

function renderInstructions() {
  const { os, browser } = refinedDevice();
  const block = data.instructions;
  const osBlock = block[os] || block.default || {};
  const entry = osBlock[browser] || osBlock.default || (block.default && block.default.default);
  if (!entry) return;
  document.getElementById("instr-title").textContent = entry.title;
  const ol = document.getElementById("instr-steps");
  ol.replaceChildren(...entry.steps.map((s) => { const li = document.createElement("li"); li.textContent = s; return li; }));
  document.getElementById("instr-audio").textContent = entry.audio || "";
  document.getElementById("instr-note").textContent = entry.note || "";
  if (data.share_type === "screen" && (!canScreen || entry.unavailable)) {
    const alt = document.getElementById("camera-instead");
    if (alt) alt.hidden = false;
  }
}

// ---------------------------------------------------------------- websocket
function send(msg) { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg)); }

async function hello() {
  const caps = { maxTouchPoints: navigator.maxTouchPoints || 0, canScreen, hints: null };
  try {
    if (navigator.userAgentData && navigator.userAgentData.getHighEntropyValues) {
      caps.hints = await navigator.userAgentData.getHighEntropyValues(["platform", "platformVersion", "architecture", "bitness", "model"]);
      caps.hints.mobile = navigator.userAgentData.mobile;
    }
  } catch { /* not available */ }
  send({ type: "hello", caps });
}

function connect() {
  ws = new WebSocket(`wss://${location.host}/ws/customer`);
  ws.addEventListener("open", () => { backoff = 1000; hello(); heartbeat(); });
  ws.addEventListener("message", (e) => {
    let m; try { m = JSON.parse(e.data); } catch { return; }
    handlers[m.type] && handlers[m.type](m);
  });
  ws.addEventListener("close", (e) => {
    if (ended) return;
    if (e.code === 4401) { showEnded("This session has ended."); return; }
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 10000);
  });
}

function heartbeat() {
  const live = [...publishers.values()].filter((p) => p.live).map((p) => p.frame.id);
  send({ type: "hb", phase, live });
}

const handlers = {
  viewers(m) {
    const names = m.names || [];
    viewersText.textContent = names.length
      ? `${joinNames(names)} from ${data.company} ${names.length === 1 ? "is" : "are"} viewing your ${data.share_type === "camera" ? "camera" : "screen"}`
      : "No one is viewing yet";
  },
  ended(m) { showEnded(m.message || "This session has ended."); },
  frame(m) { addFrame(m.frame); },
  warning(m) {
    if (!m.kind) { warningEl.hidden = true; return; }
    if (m.kind === "tech_absent") {
      warningEl.hidden = false;
      warningEl.textContent = `Your technician has lost connection. This session will end in ${m.seconds_left} seconds unless they come back.`;
    }
  },
  error(m) { alertBox(m.message); },
  apply(m) { applySettings(m); },
  rustdesk(m) { import("./customer-rustdesk.js").then((mod) => mod.showPrompt(m, send)); },
};

function joinNames(names) {
  if (names.length === 1) return names[0];
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

function alertBox(text) {
  warningEl.hidden = false;
  warningEl.textContent = text;
  setTimeout(() => { warningEl.hidden = true; }, 6000);
}

// ---------------------------------------------------------------- frames
function addFrame(frame) {
  if (publishers.has(frame.id)) return;
  const p = new Publisher(framesEl, frame, {
    onLive(audio) {
      phase = "sharing";
      send({ type: "stream", stream: frame.id, event: "live", audio });
      updateUi();
    },
    onAudio(audio) { if (audio !== null) send({ type: "stream", stream: frame.id, event: "audio", audio }); },
    onStopped() {
      send({ type: "stream", stream: frame.id, event: "stopped" });
      p.destroy();
      publishers.delete(frame.id);
      updateUi();
    },
    onCancelled() { updateUi(); },
  });
  publishers.set(frame.id, p);
  updateUi();
}

function updateUi() {
  if (ended) return;
  const all = [...publishers.values()];
  const live = all.filter((p) => p.live);
  const screens = all.filter((p) => p.frame.share_type === "screen");
  if (live.length) {
    statusText.textContent = data.share_type === "camera" ? "You are sharing your camera" : `You are sharing ${live.length === 1 ? "your screen" : live.length + " screens"}`;
  } else if (all.length) {
    statusText.textContent = data.share_type === "camera" ? "Allow your camera to start" : "Press Select screen to share to start";
  } else {
    statusText.textContent = "You are not sharing";
  }
  btnStop.hidden = live.length === 0;
  btnAgain.hidden = all.length > 0;
  btnAdd.hidden = !(data.share_type === "screen" && canScreen && live.length > 0 && screens.length < data.max_screens);
}

function stopAll() {
  for (const [id, p] of publishers) {
    if (p.live) send({ type: "stream", stream: id, event: "stopped" });
    p.destroy();
  }
  publishers.clear();
  updateUi();
}

function applySettings(m) {
  // Framerate is publisher-only. VDO.Ninja's Companion command
  // videoConstraint applies it to the live capture track (applyConstraints),
  // so there is no reload and no new screen picker. See docs/vdo-notes.md.
  const p = publishers.get(m.stream);
  if (p && m.fps) p.send({ action: "videoConstraint", value: "frameRate", value2: String(m.fps) });
}

function showEnded(message) {
  if (ended) return;
  ended = true;
  for (const p of publishers.values()) p.destroy();
  publishers.clear();
  document.querySelector(".share-layout").hidden = true;
  document.getElementById("ended").hidden = false;
  document.getElementById("ended-message").textContent = message;
  try { ws && ws.close(); } catch { /* ignore */ }
}

// ---------------------------------------------------------------- buttons
btnStop.addEventListener("click", stopAll);
btnAgain.addEventListener("click", () => send({ type: "new_stream", share_type: data.share_type }));
btnAdd.addEventListener("click", () => send({ type: "new_stream", share_type: "screen" }));
endForm.addEventListener("submit", (e) => {
  e.preventDefault();
  if (!confirm("End this session? Your technician will no longer see your " + (data.share_type === "camera" ? "camera." : "screen."))) return;
  send({ type: "end" });
  // If the websocket is down, fall back to the form post.
  setTimeout(() => { if (!ended) endForm.submit(); }, 1500);
});

renderInstructions();
data.frames.forEach(addFrame);
connect();
setInterval(heartbeat, 10000);
