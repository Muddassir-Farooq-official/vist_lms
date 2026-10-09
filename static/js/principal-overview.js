(function () {
  "use strict";

  // Institute Overview (principal + administration): GET /api/v1/reports/institute-overview
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const money = function (v) { return "Rs. " + Number(v || 0).toLocaleString("en-PK", { maximumFractionDigits: 0 }); };
  const rows = function (pairs) {
    return pairs.map(function (p) { return "<tr><td>" + esc(p[0]) + '</td><td class="num">' + esc(String(p[1])) + "</td></tr>"; }).join("");
  };

  async function load() {
    const r = await VA.fetchJson("/api/v1/reports/institute-overview");
    if (!r.ok) {
      const box = document.getElementById("ov-error");
      box.textContent = (r.body && r.body.error && r.body.error.message) || "Could not load the overview.";
      box.hidden = false;
      return;
    }
    const d = r.body;
    document.getElementById("ov-terms").textContent = d.open_terms.length ? "Open terms: " + d.open_terms.join(", ") : "No open term. Add one in Courses \u2192 Academic Terms.";
    document.getElementById("ov-active").textContent = String(d.students.by_status.ACTIVE || 0);
    document.getElementById("ov-att").textContent = d.attendance.average_percent === null ? "\u2014" : d.attendance.average_percent + "%";
    document.getElementById("ov-below").textContent = String(d.attendance.student_courses_below_minimum);
    document.getElementById("ov-rate").textContent = d.fees.collection_rate_percent === null ? "\u2014" : d.fees.collection_rate_percent + "%";
    document.getElementById("ov-teaching").innerHTML = rows([
      ["Course offerings running", d.teaching.course_offerings], ["Offerings without a teacher", d.teaching.without_teacher],
      ["Course enrollments", d.teaching.enrollments], ["Classes past their marking window and never marked", d.attendance.classes_left_unmarked],
      ["Minimum attendance to pass", d.attendance.minimum_percent + "%"],
    ]);
    const sb = d.results.sheets_by_status;
    document.getElementById("ov-results").innerHTML = rows([
      ["Marks being entered (draft)", sb.DRAFT || 0], ["Waiting for exam approval", sb.SUBMITTED || 0], ["Sent back to teachers", sb.RETURNED || 0],
      ["Approved, not yet published", sb.APPROVED || 0], ["Published", sb.PUBLISHED || 0], ["Courses with no result sheet yet", d.results.courses_without_a_sheet],
    ]);
    document.getElementById("ov-fees").innerHTML = rows([
      ["Charged", money(d.fees.charged)], ["Discounts / scholarships", money(d.fees.discounts)], ["Still outstanding", money(d.fees.outstanding)],
      ["Of which overdue", money(d.fees.overdue)], ["Collected this month", money(d.fees.collected_this_month)],
    ]);
    document.getElementById("ov-programs").innerHTML = rows(d.students.active_by_program.map(function (p) { return [p.program, p.students]; })) ||
      '<tr><td colspan="2">No active students yet.</td></tr>';
    document.getElementById("ov-status").textContent = "All students by status: " + Object.keys(d.students.by_status).map(function (k) {
      return k.toLowerCase() + " " + d.students.by_status[k];
    }).join(", ") + ".";
  }

  document.addEventListener("vistadmin:ready", load);
})();