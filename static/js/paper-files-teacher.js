(function () {
  "use strict";
  // Send exam papers (teacher): API /api/v1/exam-paper-files
  const VA = window.VistAdmin;
  const API = "/api/v1/exam-paper-files";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const day = function (v) { return new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }); };
  let target = null;
  function fileCell(f) {
    if (!f) return '<span class="sd-dim">Not sent</span>';
    const link = '<a href="' + API + "/files/" + f.id + '">' + esc(f.file_name) + "</a>";
    return "<strong>" + esc(f.status_name) + "</strong> · v" + f.version + " · " + link + (f.return_note ? "<small>To correct: " + esc(f.return_note) + "</small>" : "");
  }
  async function load() {
    const r = await VA.fetchJson(API + "/mine");
    $("ep-list").innerHTML = (r.ok ? r.body : []).map(function (p) {
      const f = p.file, printed = f && f.status === "PRINTED";
      const label = !f ? "Attach file" : (f.status === "RETURNED" ? "Send corrected file" : "Replace file");
      return '<tr data-id="' + p.paper_id + '"><td>' + esc(p.schedule) + (p.draft ? "<small>datesheet not yet published</small>" : "") + "</td><td>" + esc(p.course) + "</td><td>" + esc(day(p.exam_date)) + " " + esc(p.start_time) +
        "<small>" + (p.days_left === 0 ? "today" : "in " + p.days_left + " day(s)") + "</small></td><td>" + fileCell(f) + "</td><td>" +
        (printed ? '<span class="sd-dim">Printed — ask the exam office to change it</span>' : '<button type="button" class="row-action ep-send">' + label + "</button>") + "</td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">No coming exam papers for your courses.</td></tr>';
  }
  $("ep-list").addEventListener("click", function (e) {
    const b = e.target.closest(".ep-send");
    if (!b) return;
    target = b.closest("tr").getAttribute("data-id");
    $("ep-file").value = ""; $("ep-file").click();
  });
  $("ep-file").addEventListener("change", async function () {
    const file = $("ep-file").files[0];
    if (!file || !target) return;
    const note = await VA.ask({ title: "Send " + file.name, label: "Note for the exam office (copies, instructions) — optional", min: 0 });
    if (note === null) return;
    const fd = new FormData();
    fd.append("file", file);
    if (note) fd.append("note", note);
    const r = await VA.fetchJson(API + "/papers/" + target, { method: "POST", body: fd });
    if (!r.ok) return VA.toast(err(r, "Not sent."), "error");
    VA.toast("Sent to the exam office."); load();
  });
  load();
})();
