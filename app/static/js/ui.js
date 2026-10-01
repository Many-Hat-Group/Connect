// Small DOM helpers shared by portal pages. Text is always set with
// textContent (never innerHTML) so customer-supplied values stay inert.

export function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    n.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === "") continue;
    n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return n;
}

export function duration(iso) {
  if (!iso) return "";
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

const OS_NAMES = { windows: "Windows", macos: "Mac", linux: "Linux", chromeos: "ChromeOS", android: "Android",
  ios: "iPhone", ipados: "iPad", unknown: "Unknown device" };
const BROWSER_NAMES = { chrome: "Chrome", edge: "Edge", firefox: "Firefox", safari: "Safari", samsung: "Samsung Internet",
  opera: "Opera", unknown: "" };

export function deviceLabel(s) {
  return `${OS_NAMES[s.os] || "Unknown device"}${BROWSER_NAMES[s.browser] ? ", " + BROWSER_NAMES[s.browser] : ""}`;
}

const ICONS = {
  desktop: "M3 4h18v12H3z M8 20h8 M12 16v4",
  mobile: "M8 2h8v20H8z M11 18h2",
  tablet: "M5 2h14v20H5z M11 18h2",
};

export function osIcon(osName, deviceClass) {
  const cls = deviceClass || (osName === "ios" || osName === "android" ? "mobile" : osName === "ipados" ? "tablet" : "desktop");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", ICONS[cls] || ICONS.desktop);
  p.setAttribute("fill", "none");
  p.setAttribute("stroke", "currentColor");
  p.setAttribute("stroke-width", "2");
  svg.appendChild(p);
  return svg;
}
