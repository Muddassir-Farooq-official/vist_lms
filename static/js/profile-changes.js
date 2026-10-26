(function () {
  "use strict";

  // "Update my details" on My Profile: GET/POST /api/v1/profile-changes/me. Nothing changes on the record until the office approves.
  const VA = window.VistAdmin, esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/profile-changes/me";
  if (!$("pc-panel")) return;
  let data = null;
  const STATUS = { APPROVED: ["Approved", "active"], REJECTED: ["Not approved", "rejected"], CANCELLED: ["Withdrawn", "draft"] };

  function msg(r, fallback) { return (r.body && r.body.error && r.body.error.message) || fallback; }

  function render() {
    const p = data.pending;
    $("pc-badge").hidden = !p;
    $("pc-pending").hidden = !p;
    if (p) {
      $("pc-pending-list").innerHTML = p.changes.map(function (c) {
        return "<li><span>" + esc(c.label) + "</span><s>" + esc(c.old || "—") + "</s><b>" + esc(c.new || "(empty)") + "</b></li>";
      }).join("") || (p.has_photo ? "" : "<li><span>Only a note</span></li>");
      $("pc-pending-photo").hidden = !p.has_photo;
      if (p.has_photo) $("pc-pending-img").src = API + "/photo?t=" + Date.now();
    }
    // Each field shows what is on record, or what is already waiting for approval.
    const waiting = {};
    (p ? p.changes : []).forEach(function (c) { waiting[c.field] = c.new; });
    $("pc-fields").innerHTML = data.fields.map(function (f) {
      const val = f.key in waiting ? waiting[f.key] : data.values[f.key];
      const type = f.kind === "email" ? "email" : f.kind === "phone" ? "tel" : "text";
      return '<label class="' + (f.key === "address_line" ? "span-2" : "") + '">' + esc(f.label) + (f.required ? " *" : "") +
        '<input class="text-input" data-key="' + esc(f.key) + '" type="' + type + '" maxlength="' + f.max_len + '" value="' + esc(val || "") + '"' +
        (f.kind === "phone" ? ' inputmode="tel" placeholder="0300-1234567"' : "") + " />" +
        (f.key in waiting ? '<small class="pc-wait">Waiting for approval</small>' : "") + "</label>";
    }).join("");
    $("pc-note").value = (p && p.note) || "";
    const hist = data.history || [];
    $("pc-history-wrap").hidden = !hist.length;
    $("pc-history").innerHTML = hist.map(function (h) {
      const s = STATUS[h.status] || [h.status, ""];
      const what = h.changes.map(function (c) { return c.label; }).concat(h.has_photo || (h.photo_check && h.status !== "CANCELLED") ? ["photo"] : []).join(", ") || "details";
      return '<li><div><span class="pl-body"><strong>' + esc(what) + "</strong><small>" + esc(VA.formatDateTime(h.created_at)) +
        (h.decision_reason ? " · Reason: " + esc(h.decision_reason) : "") + '</small></span><span class="status-pill ' + s[1] + '">' + esc(s[0]) + "</span></div></li>";
    }).join("");
  }

  async function load() {
    const r = await VA.fetchJson(API, { quiet: true });
    if (!r.ok) { $("pc-panel").hidden = true; return; }
    data = r.body; render();
  }

  $("pc-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    $("pc-error").hidden = true;
    const values = {};
    $("pc-fields").querySelectorAll("input[data-key]").forEach(function (i) { values[i.getAttribute("data-key")] = i.value.trim(); });
    const btn = $("pc-submit"); btn.disabled = true;
    const r = await VA.fetchJson(API, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values: values, note: $("pc-note").value.trim() }) });
    btn.disabled = false;
    if (!r.ok) { $("pc-error").textContent = msg(r, "Could not send it."); $("pc-error").hidden = false; return; }
    VA.toast("Sent. The office will check it; you will get a notification.");
    load();
  });
  $("pc-withdraw").addEventListener("click", async function () {
    if (!data || !data.pending) return;
    if (!(await VA.confirm("Withdraw everything you sent for approval?", "Withdraw"))) return;
    const r = await VA.fetchJson(API + "/" + data.pending.id + "/withdraw", { method: "POST" });
    if (!r.ok) return VA.toast(msg(r, "Could not withdraw it."), "error");
    VA.toast("Withdrawn."); load();
  });
  $("pc-photo-drop").addEventListener("click", async function () {
    const r = await VA.fetchJson(API + "/photo", { method: "DELETE" });
    if (!r.ok) return VA.toast(msg(r, "Could not take it back."), "error");
    load();
  });

  document.addEventListener("profile-changes:refresh", load);
  document.addEventListener("vistadmin:ready", load);
})();
