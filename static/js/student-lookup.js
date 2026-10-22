(function () {
  "use strict";
  // Read-only student lookup for the principal, HOD and exam office.
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const STATUS = { ACTIVE: "Active", SUSPENDED: "Suspended", GRADUATED: "Graduated", DROPPED: "Left" };

  async function search() {
    const q = $("sl-q").value.trim();
    const tb = $("sl-table").tBodies[0];
    if (q.length < 2) { tb.innerHTML = ""; $("sl-empty").textContent = "Type at least 2 letters of a name, a roll number or a CNIC."; $("sl-empty").hidden = false; return; }
    const r = await VA.fetchJson("/api/v1/students?page_size=50&q=" + encodeURIComponent(q));
    if (!r.ok) {
      tb.innerHTML = "";
      $("sl-empty").textContent = (r.body && r.body.error && r.body.error.message) || "Could not search right now. Try again.";
      $("sl-empty").hidden = false;
      return;
    }
    const items = r.body.items || [];
    $("sl-empty").hidden = items.length > 0;
    $("sl-empty").textContent = "No student matches “" + q + "”.";
    tb.innerHTML = items.map(function (s) {
      return "<tr><td>" + esc(s.roll_number || "—") + "</td><td>" + esc(s.first_name + " " + s.last_name) + "</td><td>" + esc(s.current_semester || "—") +
        "</td><td>" + esc(s.section_label || "—") + "</td><td>" + esc(STATUS[s.status] || s.status) +
        '</td><td><button type="button" class="role-btn small-btn" data-open="' + esc(s.id) + '">Open</button></td></tr>';
    }).join("");
  }

  async function open(id) {
    const r = await VA.fetchJson("/api/v1/students/" + id + "/full-profile");
    if (!r.ok) return VA.toast((r.body && r.body.error && r.body.error.message) || "Could not open this record.", "error");
    const p = r.body;
    $("sl-title").textContent = p.first_name + " " + p.last_name + (p.roll_number ? " (" + p.roll_number + ")" : "");
    $("sl-meta").textContent = [p.program_name, p.current_semester ? "Semester " + p.current_semester : "", p.section_label ? "Section " + p.section_label : "",
      STATUS[p.status] || p.status, p.contact_no].filter(Boolean).join(" · ");
    const f = p.fees || {}, fi = p.fines || {};
    $("sl-kpis").innerHTML = [["Fees outstanding", VA.formatMoney(f.total_outstanding || 0)], ["Fees paid", VA.formatMoney(f.total_paid || 0)],
      ["Fines pending", VA.formatMoney(fi.total_pending || 0)], ["Library overdue", (p.library || {}).overdue_loans || 0]].map(function (x) {
      return '<div class="kpi"><div class="kpi-label">' + esc(x[0]) + '</div><div class="kpi-value">' + esc(String(x[1])) + "</div></div>";
    }).join("");
    $("sl-guardian").innerHTML = (p.guardians || []).map(function (g) {
      return '<div class="auth-row"><span class="grow">' + esc(g.name) + " (" + esc(g.relation || "guardian") + ")</span><span>" + esc(g.contact_no || "") + "</span></div>";
    }).join("") || '<p class="cc-meta">No guardian on record.</p>';
    const rows = (p.enrollments || []).slice().sort(function (a, b) { return String(b.term_label).localeCompare(String(a.term_label)); });
    $("sl-courses").tBodies[0].innerHTML = rows.length ? rows.map(function (e) {
      return "<tr><td>" + esc(e.term_label) + "</td><td>" + esc(e.course_code + " " + e.course_title) + (e.is_retake ? " (retake)" : "") + "</td><td>" + esc(e.status) + "</td><td>" + esc(e.outcome || "—") + "</td></tr>";
    }).join("") : '<tr><td colspan="4">No courses yet.</td></tr>';
    $("sl-transcript").href = "/api/v1/results/students/" + id + "/transcript.pdf";
    $("sl-modal").showModal();
  }

  $("sl-form").addEventListener("submit", function (e) { e.preventDefault(); search(); });
  $("sl-table").addEventListener("click", function (e) { const b = e.target.closest("[data-open]"); if (b) open(b.getAttribute("data-open")); });
  $("sl-close").addEventListener("click", function () { $("sl-modal").close(); });
})();