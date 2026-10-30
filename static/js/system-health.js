(function () {
  "use strict";
  // Admin dashboard: are the background services running, and is anything waiting too long? (GET /api/v1/system/status)
  const VA = window.VistAdmin, esc = VA.escapeHtml;
  const box = document.getElementById("sys-health");
  if (!box) return;
  function ago(s) { return s == null ? "never" : s < 90 ? "just now" : s < 3600 ? Math.round(s / 60) + " min ago" : s < 172800 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " days ago"; }
  function dot(ok) { return '<i class="sh-dot ' + (ok ? "ok" : "bad") + '"></i>'; }
  async function load() {
    const r = await VA.fetchJson("/api/v1/system/status", { quiet: true });
    if (!r.ok) { box.hidden = true; return; }
    const s = r.body;
    box.hidden = false;
    box.className = "sys-health " + (s.ok ? "ok" : "warn");
    const items = [
      dot(s.import_worker.running) + "Import worker " + (s.import_worker.running ? "running" : "not running"),
      dot(s.sender.running || s.web_app_sender.active) + "SMS &amp; email sender " + (s.sender.running ? "running (seen " + ago(s.sender.last_seen_seconds_ago) + ")"
        : s.web_app_sender.active ? "covered by the web app" : "not running"),
      dot(!s.email.enabled || !s.email.failed_24h) + "Email " + (s.email.enabled ? "on · " + s.email.waiting + " waiting" : '<a href="/dashboard/admin/settings#email">off (set it up)</a>'),
      dot(!s.sms.waiting || (s.sms.oldest_minutes || 0) < 60) + "SMS " + (s.sms.provider_configured ? "" : "not connected · ") + s.sms.waiting + " waiting",
    ];
    box.innerHTML = '<div class="sh-row"><strong>' + (s.ok ? "Background services are working" : "Background services need attention") + "</strong>" +
      '<button type="button" class="link-btn" id="sh-refresh">Check again</button></div><div class="sh-items">' + items.map(function (x) { return "<span>" + x + "</span>"; }).join("") + "</div>" +
      (s.problems.length ? "<ul>" + s.problems.map(function (p) { return "<li>" + esc(p) + "</li>"; }).join("") + "</ul>" : "");
    box.querySelector("#sh-refresh").addEventListener("click", load);
  }
  document.addEventListener("vistadmin:ready", load);
  setInterval(function () { if (!document.hidden) load(); }, 120000);
})();