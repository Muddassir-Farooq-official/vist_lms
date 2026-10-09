(function () {
  "use strict";

  // Teacher: GET /api/v1/exams/teaching/papers
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;

  async function load() {
    const holder = document.getElementById("duty-list");
    const r = await VA.fetchJson("/api/v1/exams/teaching/papers");
    if (!r.ok) { holder.innerHTML = '<p class="home-empty">Could not load your exam duties.</p>'; return; }
    if (!r.body.length) { holder.innerHTML = '<p class="empty-state">No published exam papers for your courses yet.</p>'; return; }
    holder.innerHTML = r.body.map(function (s) {
      return '<section class="sh-card"><div class="sh-card-head"><h2>' + esc(s.name + " \u2014 " + s.term_label) + "</h2></div>" +
        '<table class="dash-table"><thead><tr><th>Date</th><th>Time</th><th>Course</th><th>Room</th><th>Invigilator</th></tr></thead><tbody>' +
        s.papers.map(function (p) {
          return "<tr><td>" + esc(p.exam_date) + "</td><td>" + esc(p.start_time + "\u2013" + p.end_time) + "</td><td>" +
            esc(p.course_code + " " + p.course_title) + "</td><td>" + esc(p.room) + "</td><td>" + esc(p.invigilator || "\u2014") + "</td></tr>";
        }).join("") + "</tbody></table></section>";
    }).join("");
  }

  document.addEventListener("vistadmin:ready", load);
})();