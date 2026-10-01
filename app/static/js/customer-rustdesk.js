// Remote-control prompt on the customer page (RustDesk). Downloads are served
// from our own mirror and need the customer's session cookie.
const PLATFORM_NAMES = { windows: "Windows", macos: "Mac", linux: "Linux", android: "Android" };

function node(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null && v !== false) n.setAttribute(k, v === true ? "" : v);
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== "") n.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return n;
}

function dlUrl(platform, arch, kind) {
  return `/dl/rustdesk/${platform}/${arch}${kind ? "?kind=" + kind : ""}`;
}

function copyRow(label, value) {
  const code = node("code", { class: "secret" }, value);
  const btn = node("button", { type: "button", class: "btn small" }, "Copy");
  btn.addEventListener("click", async () => { try { await navigator.clipboard.writeText(value); btn.textContent = "Copied"; } catch { /* blocked */ } });
  return node("p", {}, node("strong", {}, `${label}: `), code, " ", btn);
}

export function showPrompt(m, send) {
  const root = document.getElementById("modal-root");
  root.replaceChildren();
  const close = () => root.replaceChildren();
  const c = m.choice || {};
  const box = node("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "rd-title" });
  box.append(node("h2", { id: "rd-title" }, `${m.tech} would like to control your computer`));
  box.append(node("p", {}, "This uses a small program called RustDesk. Your technician can then use your mouse and keyboard to help you. You can close RustDesk at any time to stop."));
  box.append(node("p", { class: "alert warning-box" }, node("strong", {}, `Only continue if you are on the phone with ${m.company}.`)));

  if (!c.available) {
    box.append(node("p", {}, c.reason || "Remote control isn't available on this device."));
  } else {
    const has = (p, a, k) => (m.downloads || []).some((d) => d.platform === p && d.arch === a && (!k || d.kind === k));
    let platform = c.platform;
    let arch = c.arch;
    const main = node("a", { class: "btn primary big", id: "rd-download" }, "Download");
    const setMain = () => {
      const ok = platform && has(platform, arch);
      main.hidden = !ok;
      if (ok) {
        main.href = dlUrl(platform, arch);
        main.textContent = `Download for ${PLATFORM_NAMES[platform]}${platform === "macos" ? (arch === "arm64" ? " (Apple chip)" : " (Intel)") : ""}`;
      }
    };
    if (c.ask_mac_chip) {
      const q = node("fieldset", {}, node("legend", {}, "Which Mac do you have?"));
      for (const [val, label] of [["arm64", "Apple chip (M1 or later)"], ["x86_64", "Intel"]]) {
        const input = node("input", { type: "radio", name: "mac-chip", value: val, checked: val === "arm64" });
        input.addEventListener("change", () => { arch = val; setMain(); });
        q.append(node("label", { class: "check" }, input, label));
      }
      q.append(node("p", { class: "small muted" }, "Not sure? Click the Apple menu, then About This Mac. It says Chip (Apple) or Processor (Intel)."));
      box.append(q);
    }
    setMain();
    main.addEventListener("click", () => send({ type: "rustdesk", action: "downloading" }));
    box.append(main);
    const steps = node("ol", {});
    if (platform === "windows") {
      steps.append(node("li", {}, "Open the file you just downloaded. If Windows asks, choose Run."),
        node("li", {}, "Read out the ID number shown in RustDesk to your technician."),
        node("li", {}, "When RustDesk asks, click Accept to let your technician connect."));
    } else if (platform === "android") {
      steps.append(node("li", {}, "Open the downloaded file and allow installs from your browser if asked."),
        node("li", {}, "Open RustDesk and allow the permissions it asks for (screen capture and accessibility). These let your technician see and control your phone."),
        node("li", {}, "Read out your ID to your technician and tap Accept when they connect."));
    } else {
      steps.append(node("li", {}, "Open the downloaded file and install RustDesk."),
        node("li", {}, "In RustDesk, open Settings, then Network, then ID/Relay server, and enter the details below."),
        node("li", {}, "Read out the ID number to your technician and click Accept when they connect."));
      if (platform === "macos") steps.append(node("li", {}, "Your Mac will ask for Screen Recording and Accessibility permission for RustDesk. Turn both on in System Settings, Privacy and Security."));
    }
    box.append(node("h3", {}, "Then"), steps);
    if (platform !== "windows" && m.host) {
      box.append(copyRow("ID server", m.host));
      if (m.relay) box.append(copyRow("Relay server", m.relay));
      if (m.key) box.append(copyRow("Key", m.key));
    }
    const others = node("details", {}, node("summary", {}, "Other downloads"));
    const ul = node("ul", {});
    for (const d of m.downloads || []) ul.append(node("li", {}, node("a", { href: dlUrl(d.platform, d.arch, d.kind) }, d.label)));
    others.append(ul);
    box.append(others);
  }
  const decline = node("button", { type: "button", class: "btn", id: "rd-decline" }, c.available ? "No thanks" : "Close");
  decline.addEventListener("click", () => { send({ type: "rustdesk", action: "declined" }); close(); });
  box.append(node("div", { class: "row" }, decline));
  const backdrop = node("div", { class: "modal-backdrop" }, box);
  root.append(backdrop);
  (box.querySelector("#rd-download") || decline).focus();
}
