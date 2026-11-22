(function () {
  "use strict";
  // Exam paper files (exam office / administration): API /api/v1/exam-paper-files
  const VA = window.VistAdmin;
  const API = "/api/v1/exam-paper-files";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const day = function (v) { return new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }); };
  let target = null;
  async function schedules() {
    const r = await VA.fetchJson(API + "/schedules");
    $("pf-schedule").innerHTML = (r.ok ? r.body : []).map(function (s) {
      return '<option value="' + s.id + '">' + esc(s.name) + (s.status === "DRAFT" ? " (draft)" : "") + " — " + s.papers + " paper(s)</option>";
    }).join("") || '<option value="">No datesheets yet</option>';
    load();
  }
  async function load() {
    if (!$("pf-schedule").value) { $("pf-list").innerHTML = ""; return; }
    const r = await VA.fetchJson(API + "/schedules/" + $("pf-schedule").value);
    if (!r.ok) return;
    const c = r.body.count;
    $("pf-count").textContent = (c.missing || 0) + " not sent · " + (c.SENT || 0) + " waiting to print · " + (c.PRINTED || 0) + " printed · " + (c.RETURNED || 0) + " returned";
    $("pf-list").innerHTML = r.body.papers.map(function (p) {
      const f = p.file;
      let cell = '<span class="sd-dim">Not sent</span>', act = "";
      if (f) {
        cell = "<strong>" + esc(f.status_name) + "</strong> · v" + f.version + ' · <a href="' + API + "/files/" + f.id + '">' + esc(f.file_name) + "</a>" + (f.note ? "<small>" + esc(f.note) + "</small>" : "") +
          (f.return_note ? "<small>Returned: " + esc(f.return_note) + "</small>" : "");
        if (f.status === "SENT") act = '<button type="button" class="row-action pf-print" data-f="' + f.id + '">Printed</button> <button type="button" class="row-action pf-return" data-f="' + f.id + '">Return…</button> ';
      }
      act += '<button type="button" class="row-action pf-up">' + (f ? "Replace" : "Attach") + "</button>";
      return '<tr data-id="' + p.paper_id + '"><td>' + esc(day(p.exam_date)) + " " + esc(p.start_time) + "<small>" + (p.days_left < 0 ? "done" : p.days_left === 0 ? "today" : "in " + p.days_left + " day(s)") + "</small></td><td>" +
        esc(p.course) + "</td><td>" + esc(p.teacher || "—") + "</td><td>" + cell + "</td><td>" + act + "</td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">This datesheet has no papers.</td></tr>';
  }
  $("pf-schedule").addEventListener("change", load);
  $("pf-list").addEventListener("click", async function (e) {
    const pr = e.target.closest(".pf-print"), rt = e.target.closest(".pf-return"), up = e.target.closest(".pf-up");
    if (up) { target = up.closest("tr").getAttribute("data-id"); $("pf-file").value = ""; $("pf-file").click(); return; }
    if (!pr && !rt) return;
    let note = null;
    if (rt) { note = await VA.ask({ title: "Return to the teacher", label: "What must be corrected", min: 5 }); if (!note) return; }
    const b = pr || rt;
    const r = await VA.fetchJson(API + "/files/" + b.getAttribute("data-f") + "/decide", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ printed: !!pr, note: note }) });
    if (!r.ok) return VA.toast(err(r, "Not saved."), "error");
    VA.toast(pr ? "Marked printed. The teacher is told." : "Returned. The teacher is told."); load();
  });
  $("pf-file").addEventListener("change", async function () {
    const file = $("pf-file").files[0];
    if (!file || !target) return;
    const fd = new FormData();
    fd.append("file", file);
    const r = await VA.fetchJson(API + "/papers/" + target, { method: "POST", body: fd });
    if (!r.ok) return VA.toast(err(r, "Not sent."), "error");
    VA.toast("File attached."); load();
  });
  schedules();
})();
