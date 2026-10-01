// Relay-only ICE test from the admin's browser: succeeds if a relay
// candidate is gathered through our TURN server.
const btn = document.getElementById("relay-run");
const result = document.getElementById("relay-result");
const detail = document.getElementById("relay-detail");

btn.addEventListener("click", async () => {
  result.textContent = "Running"; result.className = "pill";
  const cfg = await (await fetch("/admin/health/turn-test", { credentials: "same-origin" })).json();
  const pc = new RTCPeerConnection({ iceServers: cfg.iceServers, iceTransportPolicy: "relay" });
  const found = [];
  const done = new Promise((resolve) => {
    pc.onicecandidate = (e) => {
      if (e.candidate && e.candidate.type === "relay") found.push(`${e.candidate.protocol} ${e.candidate.address}:${e.candidate.port}`);
      if (!e.candidate) resolve();
    };
    setTimeout(resolve, 8000);
  });
  pc.createDataChannel("probe");
  await pc.setLocalDescription(await pc.createOffer());
  await done;
  pc.close();
  if (found.length) {
    result.textContent = "OK"; result.className = "pill live";
    detail.textContent = `Relay candidates: ${found.join(", ")}`;
  } else {
    result.textContent = "Problem"; result.className = "pill warn";
    detail.textContent = "No relay candidate. Check the TURN ports (3478 UDP/TCP, 5349 TCP, relay range) and the shared secret.";
  }
});
