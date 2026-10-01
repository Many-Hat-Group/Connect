// Customer entry pages: code boxes and screen-share capability detection.
const ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ";
const canScreen = !!(navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia);

if (!canScreen) {
  const note = document.getElementById("no-screen-note");
  if (note) note.hidden = false;
  for (const id of ["choose-screen", "screen-alt"]) {
    const el = document.getElementById(id);
    if (el) el.hidden = true;
  }
  // Someone opened /screen on a phone: send them to the camera option.
  const shareInput = document.querySelector('input[name="share_type"][value="screen"]');
  if (shareInput) {
    shareInput.value = "camera";
    const h1 = document.querySelector("h1");
    if (h1) h1.textContent = "Share your camera";
  }
}

const boxes = [...document.querySelectorAll(".code-box")];
const hint = document.getElementById("code-hint");
const defaultHint = hint ? hint.textContent : "";

function setHint(text, bad) {
  if (!hint) return;
  hint.textContent = text;
  hint.classList.toggle("bad", !!bad);
}

function clean(ch) {
  const c = (ch || "").toUpperCase();
  if (c === "I" || c === "L" || c === "O") {
    setHint(`Codes never use the letter ${c}. Check the code with your technician.`, true);
    return "";
  }
  if (c === "0") { setHint("That looks like a zero. Codes only use letters.", true); return ""; }
  if (!ALPHABET.includes(c)) return "";
  setHint(defaultHint, false);
  return c;
}

boxes.forEach((box, i) => {
  box.addEventListener("input", () => {
    const chars = [...box.value].map(clean).filter(Boolean);
    box.value = chars[0] || "";
    // Typed or pasted several letters: spread them over the next boxes.
    for (let j = 1; j < chars.length && i + j < boxes.length; j++) boxes[i + j].value = chars[j];
    const next = boxes[Math.min(i + chars.length, boxes.length - 1)];
    if (chars.length && next) next.focus();
    if (boxes.every((b) => b.value) && i + chars.length >= boxes.length) {
      const name = document.querySelector('input[name="name"]');
      if (name && !name.value) name.focus();
    }
  });
  box.addEventListener("keydown", (e) => {
    if (e.key === "Backspace" && !box.value && i > 0) { boxes[i - 1].focus(); boxes[i - 1].value = ""; e.preventDefault(); }
    if (e.key === "ArrowLeft" && i > 0) boxes[i - 1].focus();
    if (e.key === "ArrowRight" && i < boxes.length - 1) boxes[i + 1].focus();
  });
  box.addEventListener("paste", (e) => {
    const text = (e.clipboardData || window.clipboardData).getData("text");
    const chars = [...text].map(clean).filter(Boolean).slice(0, boxes.length);
    if (!chars.length) return;
    e.preventDefault();
    chars.forEach((c, j) => { boxes[j].value = c; });
    boxes[Math.min(chars.length, boxes.length - 1)].focus();
  });
});

const form = document.getElementById("entry-form");
if (form) {
  form.addEventListener("submit", (e) => {
    if (boxes.length && !boxes.every((b) => b.value)) {
      e.preventDefault();
      setHint("Enter all four letters of your code.", true);
      (boxes.find((b) => !b.value) || boxes[0]).focus();
    }
  });
  if (boxes.length && !boxes[0].value) boxes[0].focus();
}
