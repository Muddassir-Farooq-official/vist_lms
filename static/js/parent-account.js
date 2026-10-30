(function () {
  "use strict";
  // Parent: their own email address for notifications (PUT /api/v1/me/email-notifications {email}).
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/me/email-notifications";
  async function load() { const r = await VA.fetchJson(API, { quiet: true }); if (r.ok) $("pa-email").value = r.body.address || ""; }
  $("pa-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    $("pa-error").hidden = true;
    const r = await VA.fetchJson(API, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: $("pa-email").value.trim() }) });
    if (!r.ok) { $("pa-error").textContent = (r.body && r.body.error && r.body.error.message) || "Could not save it."; $("pa-error").hidden = false; return; }
    VA.toast(r.body.address ? "Saved. Notifications will be emailed to " + r.body.address + "." : "Email removed.");
    document.dispatchEvent(new Event("email-pref:refresh"));
  });
  document.addEventListener("vistadmin:ready", load);
})();