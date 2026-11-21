// System > Server health (app/serverhealth): the server's last report, refreshed every minute.
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  function size(b) {
    if (b == null) return "—";
    const u = ["bytes", "KB", "MB", "GB", "TB"]; let i = 0, n = Number(b);
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i += 1; }
    return (i === 0 ? n : n.toFixed(n < 10 ? 1 : 0)) + " " + u[i];
  }
  function tone(id, t) { const el = $(id); el.classList.remove("good", "warn", "bad"); if (t) el.classList.add(t); }
  function daysLeft(iso) { return iso ? Math.floor((new Date(iso).getTime() - Date.now()) / 86400000) : null; }
  function dur(s) { if (s == null) return "—"; const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600); return d ? d + " day" + (d === 1 ? "" : "s") + " " + h + " h" : h + " h " + Math.floor(s % 3600 / 60) + " min"; }

  function render(r) {
    const al = $("sh-alert");
    al.hidden = !r.problems.length;
    al.innerHTML = r.problems.length ? "<strong>Needs attention:</strong><br>" + r.problems.map(esc).join("<br>") : "";
    $("sh-ok").hidden = !(r.reported && !r.problems.length);
    const setup = $("sh-setup");
    setup.hidden = r.reported;
    setup.textContent = "The server has not reported yet. It starts within a minute of the next deploy; or, on the server: sudo bash /opt/vist-lms/scripts/server/sites.sh schedule";
    if (!r.reported) return;
    const s = r.data.server, L = r.limits;
    $("k-disk-v").textContent = s.disk_used_pct + "%";
    $("k-disk-s").textContent = size(s.disk_free) + " free of " + size(s.disk_total);
    tone("k-disk", s.disk_used_pct >= L.disk_pct ? "bad" : s.disk_used_pct >= L.disk_pct - 10 ? "warn" : "good");
    const m = s.memory || {};
    $("k-mem-v").textContent = m.used_pct == null ? "—" : m.used_pct + "%";
    $("k-mem-s").textContent = size(m.available) + " free of " + size(m.total) + (s.memory_high_since ? " · high since " + VA.formatDateTime(s.memory_high_since) : "");
    tone("k-mem", m.used_pct >= L.memory_pct ? "bad" : m.used_pct >= 80 ? "warn" : "good");
    $("k-load-v").textContent = s.load[1];
    $("k-load-s").textContent = s.cpus + " processor" + (s.cpus === 1 ? "" : "s") + " · 1 min " + s.load[0] + " · 15 min " + s.load[2];
    tone("k-load", s.load_high_since ? "bad" : s.load[1] >= s.cpus ? "warn" : "good");
    $("k-up-v").textContent = dur(s.uptime_s);
    $("k-up-s").textContent = "since the server last restarted";
    $("sites-h").textContent = r.data.owner ? "Institutes on this server" : "This institute";
    $("sh-tbody").innerHTML = (r.data.sites || []).map(function (x) {
      const st = !x.enabled ? '<span class="status-pill">Switched off</span>' : x.up ? (x.database === false ? '<span class="status-pill dropped">No database</span>' : '<span class="status-pill active">Up</span>')
        : '<span class="status-pill dropped">Down</span><small class="sd-dim" style="display:block">' + esc(x.error || ("HTTP " + x.http_status)) + "</small>";
      const dl = daysLeft(x.cert_expires);
      const cert = !x.domain ? "—" : x.cert_expires ? '<span class="' + (dl < L.cert_days ? "pt-bad" : "") + '">' + dl + " days</span> <small class=\"sd-dim\">(" + esc(VA.formatDateLong(x.cert_expires.slice(0, 10))) + ")</small>" : '<span class="pt-bad">' + esc(x.cert_error || "not read yet") + "</span>";
      return "<tr><td><strong>" + esc(x.name) + "</strong><small class=\"sd-dim\" style=\"display:block\">" + esc(x.domain || "") + "</small></td><td>" + st +
        '</td><td class="num">' + (x.response_ms == null ? "—" : x.response_ms + " ms") + "</td><td>" + esc(x.version ? "v" + x.version : "—") + (x.build ? ' <small class="sd-dim">' + esc(x.build) + "</small>" : "") +
        (x.updated_at ? '<small class="sd-dim" style="display:block">deployed ' + esc(VA.formatDateTime(x.updated_at)) + "</small>" : "") + "</td><td>" + cert +
        '</td><td class="num">' + size(x.storage_bytes) + "</td><td>" + esc(x.backup_ok_at || "none recorded") +
        (x.backup_failed_at && (!x.backup_ok_at || x.backup_failed_at > x.backup_ok_at) ? '<small class="pt-bad" style="display:block">failed ' + esc(x.backup_failed_at) + "</small>" : "") + "</td></tr>";
    }).join("");
    $("sh-when").textContent = "Last report " + Math.max(0, r.age_seconds) + " s ago (" + VA.formatDateTime(r.taken_at) + "). Backup times are the server's local time.";
  }
  let timer = null;
  async function load() {
    const r = await VA.fetchJson("/api/v1/server-health", { quiet: true });
    clearTimeout(timer);
    timer = setTimeout(load, 60000);
    if (!r.ok) { VA.showError((r.body && r.body.error && r.body.error.message) || "Could not load the server's health."); return; }
    render(r.body);
  }
  load();
})();
