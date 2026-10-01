// Share controls on the session view: all techs, invites, admin transfer
// and grant, and the access list with revoke.
import { api, notify } from "./portal.js";
import { el } from "./ui.js";

export async function initShareMenu(container, sessionId) {
  const panel = el("details", { class: "share-menu" });
  const summary = el("summary", { class: "btn" }, "Share");
  const body = el("div", { class: "share-body card" });
  panel.append(summary, body);
  container.replaceChildren(panel);
  let techs = [];
  try { techs = await api("GET", "/api/techs"); } catch { techs = []; }

  async function render() {
    let a;
    try { a = await api("GET", `/api/sessions/${sessionId}/access`); } catch { return; }
    const shared = a.visibility === "all_techs";
    const toggle = el("button", { type: "button", class: "btn small", id: "share-all" }, shared ? "Stop sharing with all techs" : "Share with all techs");
    toggle.addEventListener("click", async () => { await api("POST", `/api/sessions/${sessionId}/share-all`, shared ? { off: true } : {}); render(); });

    const pick = (id, label) => {
      const sel = el("select", { id, "aria-label": label }, el("option", { value: "" }, "Choose a tech"),
        ...techs.map((t) => el("option", { value: t.id }, `${t.name}${t.role === "admin" ? " (admin)" : ""}`)));
      return sel;
    };
    const inviteSel = pick("invite-user", "Tech to invite");
    const inviteBtn = el("button", { type: "button", class: "btn small", id: "invite-send" }, "Invite");
    inviteBtn.addEventListener("click", async () => {
      if (!inviteSel.value) return;
      try { await api("POST", `/api/sessions/${sessionId}/invite`, { user_id: inviteSel.value }); notify({ title: "Invite sent", panel: false }); }
      catch (e) { notify({ title: "Couldn't invite", body: e.message, panel: false }); }
      render();
    });

    const rows = a.entries.map((e) => {
      const li = el("li", {}, `${e.name}: ${e.kind === "invite" ? (e.status === "pending" ? "invited, waiting" : "invited, accepted") : e.kind === "transfer" ? "owner by transfer" : "given access"} `);
      const rv = el("button", { type: "button", class: "btn small", "data-revoke": e.id }, e.status === "pending" ? "Cancel" : "Remove");
      rv.addEventListener("click", async () => { await api("POST", `/api/invites/${e.id}/revoke`); render(); });
      if (e.kind !== "transfer") li.appendChild(rv);
      return li;
    });

    const parts = [toggle, el("div", { class: "row" }, inviteSel, inviteBtn)];
    if (a.can_admin) {
      const tSel = pick("transfer-user", "Transfer to");
      const tBtn = el("button", { type: "button", class: "btn small", id: "transfer-send" }, "Transfer");
      tBtn.addEventListener("click", async () => { if (tSel.value) { await api("POST", `/api/sessions/${sessionId}/transfer`, { user_id: tSel.value }); render(); } });
      const gSel = pick("grant-user", "Give access to");
      const gBtn = el("button", { type: "button", class: "btn small", id: "grant-send" }, "Give access");
      gBtn.addEventListener("click", async () => { if (gSel.value) { await api("POST", `/api/sessions/${sessionId}/grant`, { user_id: gSel.value }); render(); } });
      parts.push(el("p", { class: "muted" }, "Admin"), el("div", { class: "row" }, tSel, tBtn), el("div", { class: "row" }, gSel, gBtn));
    }
    if (rows.length) parts.push(el("ul", { class: "access-list" }, ...rows));
    body.replaceChildren(...parts);
  }
  await render();
  return { render };
}
