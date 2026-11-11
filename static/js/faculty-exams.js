(function () {
  "use strict";

  // Teacher: GET /api/v1/exams/teaching/duties (papers I am on duty for) and /teaching/papers (papers of my courses)
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;

  async function loadDuties() {
    const holder = document.getElementById("my-duties");
    const r = await VA.fetchJson("/api/v1/exams/teaching/duties");
    if (!r.ok) { holder.innerHTML = '<p class="home-empty">Could not load your exam duties.</p>'; return; }
    const list = r.body.filter(function (d) { return !d.is_past; });
    const past = r.body.length - list.length;
    if (!r.body.length) { holder.innerHTML = '<p class="empty-state">You are not on exam duty yet. You will be told when the exam office puts you on duty.</p>'; return; }
    holder.innerHTML = (list.length ? '<table class="dash-table"><thead><tr><th>Date</th><th>Time</th><th>Paper</th><th>Room</th><th>Your duty</th><th>With</th><th class="crs-actions"></th></tr></thead><tbody>' +
      list.map(function (d) {
        return '<tr' + (d.is_today ? ' class="fd-today"' : "") + "><td><strong>" + esc(d.is_today ? "Today" : VA.formatDateLong(d.exam_date)) + "</strong></td><td>" +
          esc(d.start_time + "–" + d.end_time) + "</td><td>" + esc(d.label + " " + d.course_title) + '<small class="dt-need">' + esc(d.schedule + " · " + d.term_label) +
          "</small></td><td>" + esc(d.room) + '</td><td><span class="fd-role">' + (d.role === "IN_CHARGE" ? "In charge" : "Assistant") + "</span></td><td>" +
          esc(d.with.map(function (w) { return w.name; }).join(", ") || "—") + '</td><td class="crs-actions"><button type="button" class="' +
          (d.is_today ? "role-btn small-btn" : "sd-view") + '" data-roll="' + d.paper_id + '">' + (d.is_today ? "Take exam roll" : "Exam roll") + "</button></td></tr>";
      }).join("") + "</tbody></table>" : '<p class="empty-state">No exam duty coming up.</p>') +
      (past ? '<p class="er-note">' + past + " earlier dut" + (past === 1 ? "y" : "ies") + " not shown.</p>" : "");
    holder.querySelectorAll("[data-roll]").forEach(function (b) {
      b.addEventListener("click", function () { window.VistExamRoll.open(b.dataset.roll, document.getElementById("er-box")); });
    });
  }

  async function loadPapers() {
    const holder = document.getElementById("duty-list");
    const r = await VA.fetchJson("/api/v1/exams/teaching/papers");
    if (!r.ok) { holder.innerHTML = '<p class="home-empty">Could not load the exam papers.</p>'; return; }
    if (!r.body.length) { holder.innerHTML = ""; return; }
    holder.innerHTML = r.body.map(function (s) {
      return '<section class="sh-card"><div class="sh-card-head"><h2>' + esc(s.name + " — " + s.term_label) + "</h2></div>" +
        '<table class="dash-table"><thead><tr><th>Date</th><th>Time</th><th>Course</th><th>Room</th><th>On duty</th></tr></thead><tbody>' +
        s.papers.map(function (p) {
          return "<tr><td>" + esc(VA.formatDateLong(p.exam_date)) + "</td><td>" + esc(p.start_time + "–" + p.end_time) + "</td><td>" +
            esc(p.course_code + " " + p.course_title) + "</td><td>" + esc(p.room) + "</td><td>" +
            esc((p.on_duty || []).map(function (a) { return a.name + (a.role === "IN_CHARGE" ? " (in charge)" : ""); }).join(", ") || "—") + "</td></tr>";
        }).join("") + "</tbody></table></section>";
    }).join("");
  }

  document.addEventListener("vistadmin:ready", function () { loadDuties(); loadPapers(); });
})();
