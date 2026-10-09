(function () {
  "use strict";

  // Parent portal: GET /api/v1/parents/me/children and /me/children/{id}. Read-only.
  const VA = window.VistAdmin;
  const SU = window.StudentUI;
  const esc = VA.escapeHtml;
  const picker = document.getElementById("child-picker");

  function fmt(v) { return v === null || v === undefined ? "\u2014" : Number(v).toFixed(2); }

  // Published datesheets of this child, with the admit card to download.
  async function showExams(id) {
    const card = document.getElementById("exam-card");
    const r = await VA.fetchJson("/api/v1/exams/children/" + encodeURIComponent(id) + "/datesheets");
    if (!r.ok || !r.body.length) { card.hidden = true; return; }
    document.getElementById("exam-body").innerHTML = r.body.map(function (s) {
      return "<h3>" + esc(s.name + " \u2014 " + s.term_label) + "</h3>" +
        '<table class="dash-table"><thead><tr><th>Date</th><th>Time</th><th>Course</th><th>Room</th></tr></thead><tbody>' +
        s.papers.map(function (p) {
          return "<tr><td>" + esc(p.exam_date) + "</td><td>" + esc(p.start_time + "\u2013" + p.end_time) + "</td><td>" +
            esc(p.course_code + " " + p.course_title) + "</td><td>" + esc(p.room) + "</td></tr>";
        }).join("") + "</tbody></table>" +
        '<p><a class="role-btn" target="_blank" rel="noopener" href="/api/v1/exams/children/' + encodeURIComponent(id) + "/admit-card/" + s.id + '/pdf">Download admit card</a></p>';
    }).join("");
    card.hidden = false;
  }

  async function showChild(id) {
    showExams(id);
    const r = await VA.fetchJson("/api/v1/parents/me/children/" + encodeURIComponent(id));
    if (!r.ok) return VA.showError((r.body && r.body.error && r.body.error.message) || "Could not load this child's details.");
    const d = r.body;
    const st = d.student || {};
    document.getElementById("ps-program").textContent = (st.program || "\u2014") + (st.semester ? " \u00b7 Sem " + st.semester : "");
    document.getElementById("ps-cgpa").textContent = fmt(d.results.summary.cgpa);
    document.getElementById("ps-fees").textContent = SU.money(d.fees.summary.total_to_pay || 0);
    const low = d.attendance.filter(function (a) { return a.below_minimum; }).length;
    document.getElementById("ps-low").textContent = String(low);
    document.getElementById("child-stats").hidden = false;

    document.getElementById("att-tbody").innerHTML = d.attendance.map(function (a) {
      return "<tr><td>" + esc(a.course_code + " \u2014 " + a.course_title) + '</td><td class="num">' + a.total_sessions_taken + '</td><td class="num">' +
        a.present_count + '</td><td class="num">' + (a.below_minimum ? "<strong>" + a.percentage + "%</strong> (below minimum)" : a.percentage + "%") + "</td></tr>";
    }).join("") || '<tr><td colspan="4">No current courses.</td></tr>';
    document.getElementById("att-note").textContent = "Minimum attendance to pass a course: " + d.attendance_minimum + "%.";
    document.getElementById("att-card").hidden = false;

    const terms = d.results.terms || [];
    document.getElementById("res-body").innerHTML = terms.length ? terms.map(function (t) {
      return "<h3>" + esc(t.label) + " \u00b7 GPA " + fmt(t.gpa) + "</h3><table class=\"dash-table\"><thead><tr><th>Course</th><th class=\"num\">Credits</th><th>Grade</th><th>Result</th></tr></thead><tbody>" +
        t.courses.map(function (c) {
          return "<tr><td>" + esc(c.course_code + " \u2014 " + (c.course_title || "")) + '</td><td class="num">' + esc(String(c.credit_hours)) + "</td><td>" +
            esc((c.letter_grade || "\u2014") + (c.supplementary ? " (S)" : "")) + "</td><td>" + esc(c.outcome || "") + (c.replaced ? " (retaken)" : "") + "</td></tr>";
        }).join("") + "</tbody></table>";
    }).join("") + "<p>CGPA " + fmt(d.results.summary.cgpa) + " \u00b7 credits earned " + esc(String(d.results.summary.credits_earned || 0)) + "</p>"
      : "<p>No published results yet.</p>";
    document.getElementById("res-card").hidden = false;

    const rows = [];
    (d.fees.assignments || []).forEach(function (a) {
      (a.installments || []).forEach(function (i) {
        const ch = i.challan ? '<a href="/api/v1/fees/challans/' + encodeURIComponent(i.challan.id) + '/pdf" target="_blank" rel="noopener">Challan</a>' : "\u2014";
        rows.push("<tr><td>" + esc(a.fee_name || "Fee") + " #" + esc(String(i.installment_number || "")) + "</td><td>" + esc(i.due_date || "\u2014") +
          '</td><td class="num">' + esc(SU.money(i.remaining)) + "</td><td>" + SU.pill(i.status) + "</td><td>" + ch + "</td></tr>");
      });
    });
    document.getElementById("fee-body").innerHTML = rows.length
      ? '<table class="dash-table"><thead><tr><th>Fee</th><th>Due</th><th class="num">Still to pay</th><th>Status</th><th>Challan</th></tr></thead><tbody>' + rows.join("") + "</tbody></table>"
      : "<p>No fees charged.</p>";
    document.getElementById("fee-card").hidden = false;
  }

  async function load() {
    const r = await VA.fetchJson("/api/v1/parents/me/children");
    if (!r.ok) return;
    const kids = r.body;
    if (!kids.length) { document.getElementById("no-children").hidden = false; return; }
    picker.innerHTML = kids.map(function (k) { return '<option value="' + esc(k.id) + '">' + esc(k.name + " (" + (k.roll_number || "") + ")") + "</option>"; }).join("");
    document.getElementById("child-picker-row").hidden = kids.length < 2;
    picker.addEventListener("change", function () { showChild(picker.value); });
    showChild(kids[0].id);
  }

  document.addEventListener("vistadmin:ready", load);
})();