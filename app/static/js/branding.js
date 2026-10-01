// Live preview for the branding page. Colours are applied through the
// CSSOM (style.setProperty), which the strict CSP allows.
const form = document.getElementById("branding-form");
const preview = document.getElementById("preview");
const live = document.getElementById("contrast-live");

function lum(hex) {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
function contrast(a, b) { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); }
function textOn(bg) { return contrast(bg, "#ffffff") >= contrast(bg, "#111111") ? "#ffffff" : "#111111"; }

function update() {
  const p = form.primary_colour.value, a = form.accent_colour.value;
  preview.style.setProperty("--pv-primary", p);
  preview.style.setProperty("--pv-primary-text", textOn(p));
  preview.style.setProperty("--pv-accent", a);
  preview.style.setProperty("--pv-accent-text", textOn(a));
  document.getElementById("pv-short").textContent = form.company_short.value || form.company_name.value;
  document.getElementById("pv-product").textContent = form.product_name.value;
  document.getElementById("pv-company").textContent = form.company_name.value;
  document.getElementById("pv-footer").textContent = `${form.footer_text.value} · ${form.support_phone.value}`;
  const cp = contrast(p, textOn(p)), cw = contrast(p, "#ffffff"), ca = contrast(a, textOn(a));
  const warn = [];
  if (cp < 4.5) warn.push(`Primary colour text contrast is ${cp.toFixed(1)}:1 (needs 4.5:1).`);
  if (cw < 3) warn.push(`Primary colour on white is ${cw.toFixed(1)}:1 (aim for 3:1).`);
  if (ca < 4.5) warn.push(`Accent colour text contrast is ${ca.toFixed(1)}:1 (needs 4.5:1).`);
  live.textContent = warn.length ? `Contrast check: ${warn.join(" ")}` : "Contrast check: OK for WCAG AA.";
  live.classList.toggle("bad", warn.length > 0);
}
form.addEventListener("input", update);
update();
