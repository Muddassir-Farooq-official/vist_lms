(function () {
  "use strict";
  // Settings → Email: the mail server, a test email, and the recent outbox.
  const VA = window.VistAdmin, esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  if (!$("em-form")) return;
  const API = "/api/v1/settings/email";
  const PILL = { SENT: ["active", "Sent"], PENDING: ["pending", "Waiting"], FAILED: ["dropped", "Failed"] };
  const err = function (r, f) { return (r.body && r.body.error && r.body.error.message) || f; };
  let loaded = false, me = null;

  function fill(d) {
    $("em-enabled").checked = !!d.enabled; $("em-host").value = d.host || ""; $("em-port").value = d.port || 587;
    $("em-security").value = d.security || "starttls"; $("em-user").value = d.username || ""; $("em-pass").value = "";
    $("em-from-name").value = d.from_name || ""; $("em-from").value = d.from_email || ""; $("em-reply").value = d.reply_to || "";
    $("em-notify").checked = d.notify_by_email !== false;
    $("em-pass-hint").textContent = d.password_unreadable ? "The saved password can no longer be read (the server's secret key changed). Type it again."
      : d.has_password ? "A password is saved (encrypted). Leave empty to keep it." : "Stored encrypted. For Gmail / Google Workspace create an App Password (Google Account → Security → App passwords).";
    $("em-remove-pass").hidden = !d.has_password;
    const on = d.enabled && d.host && d.from_email;
    $("em-state").className = "status-pill " + (on ? "active" : "");
    $("em-state").textContent = on ? (d.last_test_ok === false ? "On · last test failed" : "On") : "Off";
    if (!$("em-test-to").value && me && me.email) $("em-test-to").value = me.email;
    $("em-require").checked = !!d.require_verified_email;
    $("em-verified-count").textContent = d.active_accounts != null
      ? d.verified_accounts + " of " + d.active_accounts + " active accounts have a verified email." : "";
    $("em-admin-emails").value = (d.admin_emails || []).join("\n");
    const chosen = d.admin_copy_categories;                       // null = every kind
    $("em-cats").innerHTML = (d.categories || []).map(function (c) {
      const on = !chosen || chosen.indexOf(c.code) >= 0;
      return '<label class="check-row"><input type="checkbox" value="' + esc(c.code) + '"' + (on ? " checked" : "") + " /> <span>" + esc(c.label) + "</span></label>";
    }).join("");
  }
  function chosenCategories() {
    const boxes = Array.prototype.slice.call($("em-cats").querySelectorAll("input[type=checkbox]"));
    const on = boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; });
    return on.length === boxes.length ? null : on;
  }
  async function load() {
    const r = await VA.fetchJson(API, { quiet: true });
    if (r.ok) { fill(r.body); loadedRequire = !!r.body.require_verified_email; }
    outbox();
  }
  async function outbox() {
    const r = await VA.fetchJson(API + "/outbox", { quiet: true });
    if (!r.ok) return;
    const o = r.body;
    $("em-counts").textContent = "Last 24 hours: " + o.last_24h.sent + " sent, " + o.last_24h.failed + " failed · waiting now: " + o.waiting;
    $("em-retry").hidden = !o.last_24h.failed;
    $("em-outbox").innerHTML = o.items.length ? o.items.map(function (x) {
      const p = PILL[x.status] || ["", x.status];
      return "<tr><td>" + esc(x.to) + "</td><td>" + esc(x.subject) + (x.error ? "<br><small>" + esc(x.error) + "</small>" : "") + '</td><td><span class="status-pill ' + p[0] + '">' + p[1] +
        (x.kind === "TEST" ? " (test)" : x.kind === "ADMIN_COPY" ? " (copy)" : x.kind === "OTP" ? " (code)" : "") + "</span></td><td>" + esc(VA.formatDateTime(x.sent_at || x.created_at)) + "</td></tr>";
    }).join("") : '<tr><td colspan="4">No emails yet.</td></tr>';
  }
  let loadedRequire = false;
  async function save(extra) {
    $("em-error").hidden = true;
    const body = Object.assign({
      enabled: $("em-enabled").checked, host: $("em-host").value.trim() || null, port: parseInt($("em-port").value, 10) || 587,
      security: $("em-security").value, username: $("em-user").value.trim() || null, from_name: $("em-from-name").value.trim() || null,
      from_email: $("em-from").value.trim() || null, reply_to: $("em-reply").value.trim() || null, notify_by_email: $("em-notify").checked,
      password: $("em-pass").value ? $("em-pass").value : null, require_verified_email: $("em-require").checked,
      admin_emails: $("em-admin-emails").value.trim() || null, admin_copy_categories: chosenCategories() }, extra || {});
    if (body.admin_copy_categories && !body.admin_copy_categories.length && body.admin_emails) {
      $("em-error").textContent = "Tick at least one kind of notification to copy, or clear the administration addresses."; $("em-error").hidden = false; return false;
    }
    if (body.require_verified_email && !loadedRequire && !(await VA.confirm("From now on, everyone must confirm an email address with a code before using the portal. Continue?", "Require it"))) {
      return false;
    }
    $("em-save").disabled = true;
    const r = await VA.fetchJson(API, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    $("em-save").disabled = false;
    if (!r.ok) { $("em-error").textContent = err(r, "Could not save."); $("em-error").hidden = false; return false; }
    fill(r.body); loadedRequire = !!r.body.require_verified_email; VA.toast("Email settings saved."); return true;
  }
  $("em-form").addEventListener("submit", function (e) { e.preventDefault(); save(); });
  $("em-remove-pass").addEventListener("click", async function () {
    if (!(await VA.confirm("Remove the saved mail server password?", "Remove"))) return;
    save({ password: "" });
  });
  $("em-security").addEventListener("change", function () {
    const p = $("em-port");
    if ($("em-security").value === "ssl" && p.value === "587") p.value = 465;
    if ($("em-security").value === "starttls" && p.value === "465") p.value = 587;
  });
  $("em-test-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const out = $("em-test-result"); out.hidden = false; out.className = "cc-meta"; out.textContent = "Sending…";
    $("em-test-go").disabled = true;
    const r = await VA.fetchJson(API + "/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ to: $("em-test-to").value.trim() }) });
    $("em-test-go").disabled = false;
    if (!r.ok) { out.className = "dash-alert error"; out.textContent = err(r, "Could not send it."); return; }
    out.className = r.body.ok ? "success-text" : "dash-alert error";
    out.textContent = r.body.ok ? "Sent to " + r.body.to + ". Check the inbox (and the spam folder)." : "Not sent: " + r.body.error;
    load();
  });
  $("em-retry").addEventListener("click", async function () {
    const r = await VA.fetchJson(API + "/outbox/retry", { method: "POST" });
    if (r.ok) { VA.toast(r.body.queued_again + " email(s) will be tried again within a minute."); outbox(); }
  });
  document.addEventListener("click", function (e) {
    const t = e.target.closest && e.target.closest('.portal-tab[data-tab="email"]');
    if (t && !loaded) { loaded = true; load(); }
  });
  document.addEventListener("vistadmin:ready", function (e) {
    me = (e.detail && e.detail.me) || null;
    if (location.hash === "#email") { loaded = true; load(); }
  });
})();