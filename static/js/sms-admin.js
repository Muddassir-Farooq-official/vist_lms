(function () {
  "use strict";

  // Admin SMS log: /api/v1/sms
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const ABOUT = { ATTENDANCE_FINE: "Attendance fine", DATESHEET: "Datesheet", RESULTS: "Results", FEE_OVERDUE: "Overdue fee", TEST: "Test" };
  const STATUS = { QUEUED: "Waiting", SENT: "Sent", FAILED: "Failed", LOGGED: "Logged (not sent)", SKIPPED: "Skipped" };

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
    document.getElementById("sms-not-configured").hidden = d.provider_configured;
    ["sent", "queued", "failed", "logged"].forEach(function (k) {
      document.getElementById("sms-" + k).textContent = String(d.last_7_days[k.toUpperCase()] || 0);
    });
    document.getElementById("sms-tbody").innerHTML = d.messages.map(function (m) {
      return "<tr><td>" + esc(String(m.created_at).slice(0, 16).replace("T", " ")) + "</td><td>" + esc(m.to_number) +
        " <small>(" + esc(m.recipient.toLowerCase()) + ")</small></td><td>" + esc(ABOUT[m.event] || m.event) + "</td><td>" +
        esc(STATUS[m.status] || m.status) + (m.last_error ? "<br><small>" + esc(m.last_error) + "</small>" : "") + "</td><td>" + esc(m.body) + "</td></tr>";
    }).join("") || '<tr><td colspan="5">No messages yet.</td></tr>';
  }

  async function post(url, body, done) {
    const r = await VA.fetchJson(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    if (!r.ok) return say(errText(r));
    say(done(r.body));
    load();
  }

  document.getElementById("sms-send").addEventListener("click", function () {
    post("/api/v1/sms/send-now", null, function (b) { return b.processed + " processed: " + b.sent + " sent, " + b.failed + " failed, " + b.logged + " logged."; });
  });
  document.getElementById("sms-retry").addEventListener("click", function () {
    post("/api/v1/sms/retry", null, function (b) { return b.requeued + " message(s) put back in the queue."; });
  });
  document.getElementById("sms-fees").addEventListener("click", function () {
    if (!window.confirm("Queue an SMS to every student with an overdue fee (at most one per student per week)?")) return;
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