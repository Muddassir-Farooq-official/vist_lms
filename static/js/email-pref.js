(function () {
  "use strict";
  // My Profile: switch notification emails on or off (GET/PUT /api/v1/me/email-notifications).
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  if (!$("ep-panel")) return;
  const API = "/api/v1/me/email-notifications";
  function show(d) {
    $("ep-panel").hidden = false;
    $("ep-on").checked = !!d.email_notifications;
    $("ep-on").disabled = !d.address;
    $("ep-note").textContent = !d.institute_sends_email ? "The institute has not turned on email yet. Your choice is kept for when it does."
      : !d.address ? "There is no email address on your record yet. Add one first."
      : d.email_notifications ? "Notifications are sent to " + d.address + "." : "Notifications only appear here, not by email.";
  }
  async function load() { const r = await VA.fetchJson(API, { quiet: true }); if (r.ok) show(r.body); }
  $("ep-on").addEventListener("change", async function () {
    const r = await VA.fetchJson(API, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email_notifications: $("ep-on").checked }) });
    if (!r.ok) { VA.toast("Could not save it.", "error"); return load(); }
    show(r.body); VA.toast(r.body.email_notifications ? "Email notifications on." : "Email notifications off.");
  });
  document.addEventListener("vistadmin:ready", load);
  document.addEventListener("email-pref:refresh", load);
})();