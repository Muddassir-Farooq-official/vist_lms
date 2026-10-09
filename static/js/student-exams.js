(function () {
  "use strict";

  // Student: GET /api/v1/exams/me/datesheets, /me/admit-card/{id}, /me/admit-card/{id}/pdf
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;

  async function load() {
    const holder = document.getElementById("ex-list");
    const r = await VA.fetchJson("/api/v1/exams/me/datesheets");
    if (!r.ok) { holder.innerHTML = '<p class="home-empty">Could not load your exams.</p>'; return; }
    if (!r.body.length) { holder.innerHTML = '<p class="empty-state">No datesheet has been published for your courses yet.</p>'; return; }
    holder.innerHTML = "";
    for (const s of r.body) {
      const card = await VA.fetchJson("/api/v1/exams/me/admit-card/" + s.id);
      const c = card.ok ? card.body : { papers: [], withheld: [] };
      const elig = {};
      c.papers.forEach(function (p) { elig[p.id] = p; });
      const sec = document.createElement("section");
      sec.className = "sh-card";
      sec.innerHTML = '<div class="sh-card-head"><h2>' + esc(s.name + " \u2014 " + s.term_label) + "</h2></div>" +
        (c.withheld.length ? '<p class="dash-alert error">Your admit card is withheld until you clear: ' + esc(c.withheld.join("; ")) + ".</p>" : "") +
        '<table class="dash-table"><thead><tr><th>Date</th><th>Time</th><th>Course</th><th>Room</th><th>Status</th></tr></thead><tbody>' +
        s.papers.map(function (p) {
          const e = elig[p.id];
          return "<tr><td>" + esc(VA.formatDateLong ? VA.formatDateLong(p.exam_date) : p.exam_date) + "</td><td>" + esc(p.start_time + "\u2013" + p.end_time) +
            "</td><td>" + esc(p.course_code + " " + p.course_title) + "</td><td>" + esc(p.room) + "</td><td>" +
            (e && !e.eligible ? '<strong>Not eligible</strong> (' + esc(e.reason) + ")" : "Eligible") + "</td></tr>";
        }).join("") + "</tbody></table>" +
        '<p><a class="role-btn" href="/api/v1/exams/me/admit-card/' + s.id + '/pdf" target="_blank" rel="noopener">Download admit card</a></p>';
      holder.appendChild(sec);
    }
  }

  // Supplementary (reappear) papers this student is registered for, and their results once published.
  async function loadSupplementary() {
    const r = await VA.fetchJson("/api/v1/supplementary/me");
    if (!r.ok || !r.body.length) return;
    const sec = document.createElement("section");
    sec.className = "sh-card";
    sec.innerHTML = '<div class="sh-card-head"><h2>Supplementary exams</h2></div><table class="dash-table"><thead><tr><th>Course</th><th>Term</th><th>Paper</th><th>Status</th></tr></thead><tbody>' +
      r.body.map(function (x) {
        const st = x.status !== "PUBLISHED" ? "Registered \u2014 result not published yet" :
          (x.result.changed ? "Passed, grade " + x.result.letter_grade + " (" + x.result.percentage + "%)" : (x.result.absent ? "Absent \u2014 you must repeat the course" : "Not passed \u2014 you must repeat the course"));
        return "<tr><td>" + esc(x.course_code + " " + x.course_title) + "</td><td>" + esc(x.term_label) + "</td><td>" + esc(x.component) + "</td><td>" + esc(st) + "</td></tr>";
      }).join("") + "</tbody></table>";
    document.getElementById("ex-list").after(sec);
  }

  document.addEventListener("vistadmin:ready", function () { load(); loadSupplementary(); });
})();