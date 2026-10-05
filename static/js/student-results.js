(function () {
  "use strict";

  const VA = window.VistAdmin;

  function statusPillHtml(status) {
    if (!status) return "";
    return '<span class="status-pill ' + status.toLowerCase() + '">' + VA.escapeHtml(status) + "</span>";
  }

  // The result card: grades, GPA per term and the cumulative GPA. Only published results are in it.
  let card = { byEnrollment: {}, byTerm: {} };
  async function loadCard() {
    const r = await VA.fetchJson("/api/v1/results/me/transcript");
    if (!r.ok) return;
    const t = r.body, fmt = function (v) { return v === null || v === undefined ? "\u2014" : Number(v).toFixed(2); };
    t.terms.forEach(function (term) {
      card.byTerm[term.label] = term;
      term.courses.forEach(function (c) { card.byEnrollment[c.enrollment_id] = c; });
    });
    if (!t.terms.length) return;
    const s = t.summary, tile = function (a, b, c) { return '<div class="rsum"><span>' + a + "</span><strong>" + b + "</strong><small>" + (c || "") + "</small></div>"; };
    document.getElementById("rsum-tiles").innerHTML = tile("CGPA", fmt(s.cgpa), "cumulative, out of 4.00") + tile("Credit hours earned", s.credits_earned, "of " + s.credits_attempted + " attempted") +
      tile("Terms", s.terms, "with published results") + tile("To clear", s.courses_to_clear, s.courses_to_clear ? "failed, to retake" : "all courses passed");
    document.getElementById("results-summary").hidden = false;
  }

  async function loadResults() {
    await loadCard();
    const result = await VA.fetchJson("/api/v1/academics/enrollments/me");
    const list = document.getElementById("results-list");
    const empty = document.getElementById("results-empty");
    list.innerHTML = "";

    if (!result.ok) return;

    empty.hidden = result.body.length > 0;

    for (const enrollment of result.body) {
      const block = document.createElement("div");
      block.className = "panel result-course";

      let headerHtml =
        "<strong>" + VA.escapeHtml(enrollment.course_code) + " \u2014 " + VA.escapeHtml(enrollment.course_title) + "</strong>" +
        '<div class="panel-hint">' + VA.escapeHtml(enrollment.term_label) + "</div>" +
        '<div style="margin-top:6px">' + statusPillHtml(enrollment.status) +
        (enrollment.outcome ? " " + statusPillHtml(enrollment.outcome) : "") +
        (enrollment.is_retake ? ' <span class="status-pill waitlisted">RETAKE</span>' : "") + "</div>";
      const graded = card.byEnrollment[enrollment.id], termInfo = card.byTerm[enrollment.term_label];
      if (graded) {
        headerHtml += '<div class="grade-line"><span class="grade-badge ' + (graded.outcome === "PASSED" ? "pass" : "fail") + '">' + VA.escapeHtml(graded.letter_grade) + "</span>" +
          "<span>" + graded.grade_points.toFixed(2) + " grade points \u00b7 " + graded.credit_hours + " credit hour" + (graded.credit_hours > 1 ? "s" : "") + " \u00b7 " + graded.percentage.toFixed(1) + "%" +
          (graded.failed_for_attendance ? " \u00b7 failed: attendance below the limit" : "") + (graded.replaced ? " \u00b7 repeated later" : "") + "</span></div>" +
          (termInfo ? '<div class="panel-hint">Term GPA ' + (termInfo.gpa === null ? "\u2014" : termInfo.gpa.toFixed(2)) + " \u00b7 CGPA after this term " + (termInfo.cgpa_after === null ? "\u2014" : termInfo.cgpa_after.toFixed(2)) + "</div>" : "");
      }
      block.innerHTML = headerHtml;

      const marksResult = await VA.fetchJson("/api/v1/academics/enrollments/" + enrollment.id + "/marks");
      if (marksResult.ok && marksResult.body.length > 0) {
        const table = document.createElement("table");
        table.className = "dash-table";
        table.style.marginTop = "10px";
        let rowsHtml = "<thead><tr><th>Assessment</th><th>Category</th><th>Score</th></tr></thead><tbody>";
        let totalObtained = 0;
        let totalMax = 0;
        marksResult.body.forEach(function (mark) {
          rowsHtml +=
            "<tr><td>" + VA.escapeHtml(mark.assessment_type_name) + "</td>" +
            "<td>" + VA.escapeHtml(mark.category || "\u2014") + "</td>" +
            "<td>" + mark.obtained_marks + " / " + mark.max_marks + "</td></tr>";
          totalObtained += mark.obtained_marks;
          totalMax += mark.max_marks;
        });
        rowsHtml += "</tbody>";
        table.innerHTML = rowsHtml;
        block.appendChild(table);

        if (enrollment.outcome) {
          const totalP = document.createElement("p");
          totalP.className = "panel-hint";
          const pct = totalMax > 0 ? ((totalObtained / totalMax) * 100).toFixed(2) : "0";
          totalP.textContent = "Total: " + totalObtained + " / " + totalMax + " (" + pct + "%)";
          block.appendChild(totalP);
        }
      } else {
        const noneP = document.createElement("p");
        noneP.className = "panel-hint";
        noneP.textContent = enrollment.outcome ? "No marks are recorded for this subject." : "Your results for this subject have not been published yet. You will get a notification when they are.";
        block.appendChild(noneP);
      }

      list.appendChild(block);
    }
  }

  document.addEventListener("vistadmin:ready", function () {
    loadResults();
  });
})();