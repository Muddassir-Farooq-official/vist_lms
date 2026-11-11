(function () {
  "use strict";

  // Admin SMS log: /api/v1/sms
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const ABOUT = { ATTENDANCE_FINE: "Attendance fine", DATESHEET: "Datesheet", RESULTS: "Results", FEE_OVERDUE: "Overdue fee", TEST: "Test", BULK: "Group message", PASSWORD_RESET: "Password reset", ADMISSION: "Admission",
    FEE_REMINDER: "Fee reminder", FEE_PAID: "Payment received", CERTIFICATE_READY: "Certificate ready", PARENT_WEEKLY: "Monday note to parents",
    ATTENDANCE_WARNING: "Attendance warning", SECURITY: "Security alert", EXAM_DUTY: "Exam duty", WARNING: "Warning",
    SCHOOL_ABSENCE: "Absent / late (school)", SCHOOL_HOMEWORK: "Homework (school)", SCHOOL_PTM: "Parent-teacher meeting" };
  const DOC = { CHALLAN: "challan", RECEIPT: "receipt", RESULT: "result card", CERTIFICATE: "certificate" };
  const STATUS = { QUEUED: "Waiting", SENT: "Sent", FAILED: "Failed", LOGGED: "Logged (not sent)", SKIPPED: "Skipped" };
  const PILL = { QUEUED: "pending", SENT: "active", FAILED: "failed", LOGGED: "cancelled", SKIPPED: "cancelled" };

  function say(text) {
    const el = document.getElementById("sms-msg");
    el.textContent = text;
    el.hidden = false;
  }
  function errText(r) { return (r.body && r.body.error && r.body.error.message) || "That did not work."; }

  async function load() {
    const f = document.getElementById("sms-filter").value;
    const r = await VA.fetchJson("/api/v1/sms" + (f ? "?status=" + f : ""));
    if (!r.ok) return say(errText(r));
    const d = r.body;
    document.getElementById("sms-not-configured").hidden = d.provider_configured || d.whatsapp_on;
    ["sent", "queued", "failed", "logged"].forEach(function (k) {
      document.getElementById("sms-" + k).textContent = String(d.last_7_days[k.toUpperCase()] || 0);
    });
    document.getElementById("sms-tbody").innerHTML = d.messages.map(function (m) {
      const at = String(m.created_at);
      return '<tr><td><strong>' + esc(at.slice(11, 16)) + "</strong><small>" + esc(window.VistAdmin.formatDateLong(at.slice(0, 10))) + "</small></td><td>" + esc(m.to_number) +
        "<small>" + esc(m.recipient.charAt(0) + m.recipient.slice(1).toLowerCase()) + "</small></td><td>" + esc(ABOUT[m.event] || m.event) + "</td><td>" +
        '<span class="status-pill ' + (PILL[m.status] || "pending") + '">' + esc(STATUS[m.status] || m.status) + "</span>" + (m.last_error ? "<small>" + esc(m.last_error) + "</small>" : "") + '</td><td class="sms-by">' +
        esc(m.status === "SENT" ? (m.channel === "WHATSAPP" ? "WhatsApp" : "SMS") : "") +
        (m.attachment && m.channel === "WHATSAPP" ? "<small>with " + esc(DOC[m.attachment] || "document") + "</small>" : "") +
        (m.whatsapp_error && m.channel === "SMS" ? '<small title="' + esc(m.whatsapp_error) + '">WhatsApp did not take it</small>' : "") +
        '</td><td class="sms-body">' + esc(m.body) + "</td></tr>";
    }).join("") || '<tr><td colspan="6">No messages yet.</td></tr>';
  }

  // ---- a message to a chosen group
  function bulkBody(dry) {
    const aud = document.getElementById("bk-aud").value;
    const b = { audience: aud, message: document.getElementById("bk-msg").value.trim(), dry_run: dry };
    if (aud === "STAFF") {
      if (document.getElementById("bk-role").value) b.job_role_id = document.getElementById("bk-role").value;
    } else {
      if (document.getElementById("bk-prog").value) b.program_id = document.getElementById("bk-prog").value;
      if (document.getElementById("bk-sem").value) b.semester = parseInt(document.getElementById("bk-sem").value, 10);
      if (document.getElementById("bk-sec").value.trim()) b.section = document.getElementById("bk-sec").value.trim();
      b.defaulters_only = document.getElementById("bk-def").checked;
    }
    return b;
  }
  let checked = null;
  function invalidate() { checked = null; document.getElementById("bk-send").disabled = true; document.getElementById("bk-preview").hidden = true; }
  ["bk-aud", "bk-prog", "bk-sem", "bk-sec", "bk-def", "bk-role", "bk-msg"].forEach(function (id) {
    document.getElementById(id).addEventListener("input", invalidate);
    document.getElementById(id).addEventListener("change", invalidate);
  });
  document.getElementById("bk-aud").addEventListener("change", function () {
    const staff = this.value === "STAFF";
    document.querySelectorAll("#bulk-form [data-students]").forEach(function (el) { el.hidden = staff; });
    document.querySelectorAll("#bulk-form [data-staff]").forEach(function (el) { el.hidden = !staff; });
  });
  document.getElementById("bk-msg").addEventListener("input", function () { document.getElementById("bk-count").textContent = this.value.length + " / 300"; });
  async function bulk(dry) {
    const err = document.getElementById("bk-error"); err.hidden = true;
    const body = bulkBody(dry);
    if (body.message.length < 5) { err.textContent = "Write the message first."; err.hidden = false; return null; }
    const r = await VA.fetchJson("/api/v1/sms/bulk", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { err.textContent = errText(r); err.hidden = false; return null; }
    return r.body;
  }
  document.getElementById("bk-check").addEventListener("click", async function () {
    const b = await bulk(true);
    if (!b) return;
    checked = b;
    const p = document.getElementById("bk-preview");
    p.textContent = b.recipients + " phone number(s) will get it" + (b.sample.length ? " (e.g. " + b.sample.slice(0, 4).join(", ") + ")" : "") + "." +
      (b.without_valid_number ? " " + b.without_valid_number + " have no valid mobile number on record." : "");
    p.hidden = false;
    document.getElementById("bk-send").disabled = !b.recipients;
  });
  document.getElementById("bulk-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    if (!checked) return;
    if (!(await window.VistAdmin.confirm("Send this message to " + checked.recipients + " phone number(s)? It cannot be taken back."))) return;
    const b = await bulk(false);
    if (!b) return;
    say(b.queued + " message(s) queued. They go out within a minute.");
    document.getElementById("bk-msg").value = ""; invalidate(); load();
  });
  document.addEventListener("vistadmin:ready", async function (ev) {
    const perms = (ev.detail && ev.detail.me && ev.detail.me.permissions) || [];
    if (perms.indexOf("program.view") !== -1) {
      const pr = await VA.fetchJson("/api/v1/programs", { quiet: true });
      if (pr.ok) pr.body.forEach(function (x) { const o = document.createElement("option"); o.value = x.id; o.textContent = x.code + " \u2014 " + x.name; document.getElementById("bk-prog").appendChild(o); });
    }
    const jr = await VA.fetchJson("/api/v1/job-roles", { quiet: true });
    if (jr.ok) (jr.body || []).forEach(function (x) { const o = document.createElement("option"); o.value = x.id; o.textContent = x.title; document.getElementById("bk-role").appendChild(o); });
  });

  async function post(url, body, done) {
    const r = await VA.fetchJson(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    if (!r.ok) return say(errText(r));
    say(done(r.body));
    load();
  }

  document.getElementById("sms-send").addEventListener("click", function () {
    post("/api/v1/sms/send-now", null, function (b) { return b.processed + " processed: " + b.sent + " sent, " + b.failed + " failed, " + b.logged + " logged." + (b.left_in_queue ? " " + b.left_in_queue + " more will go out in the background within a minute." : ""); });
  });
  document.getElementById("sms-retry").addEventListener("click", function () {
    post("/api/v1/sms/retry", null, function (b) { return b.requeued + " message(s) put back in the queue."; });
  });
  document.getElementById("sms-fees").addEventListener("click", async function () {
    if (!(await window.VistAdmin.confirm("Queue an SMS to every student with an overdue fee (at most one per student per week)?"))) return;
    post("/api/v1/sms/fee-reminders", null, function (b) { return b.queued + " reminder(s) queued."; });
  });
  document.getElementById("sms-test").addEventListener("click", function () {
    const n = document.getElementById("sms-test-number").value.trim();
    if (!n) return say("Enter a mobile number.");
    post("/api/v1/sms/test", { number: n }, function () { return "Test SMS queued. Press Send waiting messages now to send it straight away."; });
  });
  document.getElementById("sms-filter").addEventListener("change", load);
  document.addEventListener("vistadmin:ready", load);
})();