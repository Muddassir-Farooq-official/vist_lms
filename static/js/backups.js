// Admin > Backups (app/backups/router.py): history, what is kept on the backup account, restore test, and the three
// requests the server carries out (back up now, database .dump, files ZIP).
(function () {
  "use strict";

  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (s) { return VA.escapeHtml(s == null ? "" : String(s)); };
  let state = null, timer = null, kindAsked = null;

  function size(b) {
    if (b == null) return "—";
    const u = ["bytes", "KB", "MB", "GB", "TB"];
    let i = 0, n = Number(b);
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i += 1; }
    return (i === 0 ? n : n.toFixed(n < 10 ? 1 : 0)) + " " + u[i];
  }
  function when(iso) { return iso ? VA.formatDateTime(iso) : "—"; }
  function ago(iso) {
    if (!iso) return "";
    const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    if (m < 1) return "just now";
    if (m < 60) return m + " min ago";
    const h = Math.round(m / 60);
    if (h < 48) return h + " hour" + (h === 1 ? "" : "s") + " ago";
    return Math.round(h / 24) + " days ago";
  }
  function tone(id, t) { const el = $(id); el.classList.remove("good", "warn", "bad"); if (t) el.classList.add(t); }
  const KIND = { NIGHTLY: "Nightly backup", MANUAL: "Backup (Back up now)", VERIFY: "Restore test" };
  const JOB = { PENDING: ["pending", "Waiting for the server"], RUNNING: ["pending", "Working…"], DONE: ["active", "Ready"],
                FAILED: ["dropped", "Failed"], EXPIRED: ["", "Deleted (1 hour passed)"] };

  function render(s) {
    state = s;
    const alert = $("bk-alert");
    alert.hidden = !s.health.problem;
    alert.textContent = s.health.problem || "";
    const helper = $("bk-helper");
    helper.hidden = s.helper.ok;
    helper.className = "dash-alert error";
    helper.textContent = s.helper.last_seen_at
      ? "The server's helper last checked for requests " + ago(s.helper.last_seen_at) + ", so buttons below will wait. Ask whoever looks after the server to run: sudo bash /opt/vist-lms/scripts/server/sites.sh schedule"
      : "The server's helper for this page is not set up yet, so the buttons below will wait until it is. It is set up by the next deploy, or on the server: sudo bash /opt/vist-lms/scripts/server/sites.sh schedule";

    const last = s.last_ok;
    $("k-last-v").textContent = last ? ago(last.finished_at) : "None yet";
    $("k-last-s").textContent = last ? when(last.finished_at) + " · " + (KIND[last.kind] || last.kind) : "after the next nightly backup";
    tone("k-last", s.health.problem ? "bad" : last ? "good" : "warn");
    $("k-stored-v").textContent = s.stored ? size(s.stored.bytes) : "—";
    $("k-stored-s").textContent = s.stored ? s.stored.daily + " nightly + " + s.stored.weekly + " weekly database copies, and the files · " + ago(s.stored.as_of) : "shown after the next backup";
    $("k-db-v").textContent = last ? size(last.db_bytes) : "—";
    $("k-files-v").textContent = last ? size(last.files_bytes) : "—";
    const v = s.verify;
    $("k-verify-v").textContent = !v ? "Not yet" : v.ok ? "Passed" : "FAILED";
    $("k-verify-s").textContent = !v ? "runs on the 1st of each month" : when(v.finished_at) + (v.overdue ? " · overdue" : " · monthly");
    tone("k-verify", !v ? "warn" : !v.ok ? "bad" : v.overdue ? "warn" : "good");
    const m = s.last_30_days;
    $("k-month-v").textContent = m.ok + " OK";
    $("k-month-s").textContent = m.failed ? m.failed + " failed" : "none failed";
    tone("k-month", m.failed ? "warn" : m.ok ? "good" : "");

    const jt = $("jobs-tbody");
    jt.innerHTML = s.jobs.map(function (j) {
      const st = JOB[j.status] || ["", j.status];
      let action = "";
      if (j.can_download) action = '<a class="role-btn" href="/api/v1/backups/exports/' + j.id + '/download" download>Download</a>' +
        '<div class="sd-dim">until ' + esc(new Date(j.expires_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })) + (j.downloaded_at ? " · downloaded" : "") + "</div>";
      else if (j.status === "DONE" && j.file_bytes && !j.mine) action = '<span class="sd-dim">only ' + esc(j.requested_by) + " can download it</span>";
      const note = j.status === "FAILED" || j.kind === "BACKUP_NOW" ? (j.detail ? '<div class="sd-dim">' + esc(j.detail) + "</div>" : "") : "";
      return "<tr><td>" + esc(j.label) + note + "</td><td>" + esc(j.requested_by) + "</td><td>" + esc(when(j.created_at)) + "</td>" +
        '<td><span class="status-pill ' + st[0] + '">' + esc(st[1]) + "</span></td><td class=\"num\">" + (j.file_bytes ? size(j.file_bytes) : "") +
        "</td><td>" + action + "</td></tr>";
    }).join("");
    $("jobs-empty").hidden = s.jobs.length > 0;

    const copies = s.stored ? s.stored.copies : [];
    $("copies-tbody").innerHTML = copies.map(function (c) {
      return "<tr><td><code>" + esc(c.name) + "</code></td><td>" + (c.folder === "weekly" ? "Weekly" : "Nightly") + "</td><td>" +
        esc(when(c.at)) + '</td><td class="num">' + size(c.bytes) + "</td></tr>";
    }).join("");
    $("copies-empty").hidden = copies.length > 0;

    $("runs-tbody").innerHTML = s.runs.map(function (r) {
      return "<tr><td>" + esc(when(r.finished_at)) + "</td><td>" + esc(KIND[r.kind] || r.kind) + "</td>" +
        '<td><span class="status-pill ' + (r.ok ? "active" : "dropped") + '">' + (r.ok ? (r.kind === "VERIFY" ? "Passed" : "OK") : "Failed") + "</span></td>" +
        '<td class="num">' + (r.db_bytes ? size(r.db_bytes) : "") + "</td><td>" + esc(r.detail || "") + "</td></tr>";
    }).join("");
    $("runs-empty").hidden = s.runs.length > 0;

    const open = function (k) { return s.jobs.some(function (j) { return j.kind === k && (j.status === "PENDING" || j.status === "RUNNING"); }); };
    $("bk-now").disabled = open("BACKUP_NOW");
    $("bk-db").disabled = open("EXPORT_DB");
    $("bk-files").disabled = open("EXPORT_FILES");
    clearTimeout(timer);
    timer = setTimeout(function () { load(true); }, s.busy ? 8000 : 60000);
  }

  async function load(quiet) {
    const r = await VA.fetchJson("/api/v1/backups/status", { quiet: !!quiet });
    if (!r.ok) {
      if (!quiet) VA.showError((r.body && r.body.error && r.body.error.message) || "Could not load the backups.");
      clearTimeout(timer);
      timer = setTimeout(function () { load(true); }, 60000);
      return;
    }
    VA.clearError();
    render(r.body);
  }

  $("bk-now").addEventListener("click", async function () {
    const ok = await VA.ask({ title: "Back up now?", intro: "The server makes the same backup as the nightly one (database and files, to the backup account). It starts within a minute and takes a few minutes; the site keeps working.", ok: "Back up now", input: "none" });
    if (!ok) return;
    const r = await VA.fetchJson("/api/v1/backups/backup-now", { method: "POST" });
    if (!r.ok) { VA.toast(r.body.error.message, "error"); return; }
    VA.toast("Asked. The result will show here in a few minutes.");
    load(true);
  });

  function openAuth(kind) {
    kindAsked = kind;
    $("bk-auth-title").textContent = kind === "DB" ? "Download the whole database" : "Download all uploaded files";
    $("bk-auth-intro").textContent = (kind === "DB"
      ? "One .dump file with everything in the database (students, fees, results, accounts…). "
      : "One ZIP with every uploaded file (photos, documents, receipts). It can be large. ") +
      "Type your password to confirm it is you. The other administrators will be told.";
    $("bk-code-row").hidden = !(state && state.two_factor);
    $("bk-pass").value = ""; $("bk-code").value = "";
    $("bk-auth-err").hidden = true;
    $("bk-auth").showModal();
    $("bk-pass").focus();
  }
  $("bk-db").addEventListener("click", function () { openAuth("DB"); });
  $("bk-files").addEventListener("click", function () { openAuth("FILES"); });
  $("bk-auth-cancel").addEventListener("click", function () { $("bk-auth").close(); });
  $("bk-auth-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const err = $("bk-auth-err");
    const body = { kind: kindAsked, password: $("bk-pass").value };
    if (!$("bk-code-row").hidden) body.code = $("bk-code").value.trim();
    if (!body.password) { err.textContent = "Type your password."; err.hidden = false; return; }
    if (!$("bk-code-row").hidden && !body.code) { err.textContent = "Type the 6-digit code."; err.hidden = false; return; }
    const r = await VA.fetchJson("/api/v1/backups/exports", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    $("bk-pass").value = "";
    if (!r.ok) { err.textContent = r.body.error.message; err.hidden = false; return; }
    $("bk-auth").close();
    VA.toast("Being prepared. A Download button appears below when it is ready (usually within a few minutes).");
    load(true);
  });

  load(false);
})();
