(function () {
  "use strict";
  // A student's own degree progress (degree audit). Their documents: my-documents.js.
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const STATUS = { PASSED: ["Passed", "active"], IN_PROGRESS: ["Studying now", "pending"], FAILED: ["Failed: repeat it", "dropped"], NOT_TAKEN: ["Not taken yet", ""] };
  const err = function (r, f) { return (r.body && r.body.error && r.body.error.message) || f; };

  async function audit() {
    const tb = $("da-table").tBodies[0];
    const r = await VA.fetchJson("/api/v1/records/me/degree-audit");
    if (!r.ok) {
      tb.innerHTML = '<tr><td colspan="4">' + esc(err(r, "Could not load your degree progress.")) + ' <button type="button" class="link-btn" id="da-retry">Try again</button></td></tr>';
      $("da-retry").addEventListener("click", audit);
      return;
    }
    const a = r.body;
    $("da-kpis").innerHTML = [["Credit hours passed", a.credits_passed + " of " + a.credits_required], ["CGPA", (a.cgpa || 0).toFixed(2) + " (needs " + a.min_cgpa.toFixed(2) + ")"],
      ["Courses still to pass", a.missing.length]].map(function (x) { return '<div class="kpi"><div class="kpi-label">' + esc(x[0]) + '</div><div class="kpi-value">' + esc(String(x[1])) + "</div></div>"; }).join("");
    $("da-note").className = a.clear ? "success-text" : "panel-hint";
    $("da-note").textContent = a.clear ? "You have met every requirement to graduate." : a.problems.join(" ");
    tb.innerHTML = a.courses.length ? a.courses.map(function (c) {
      const s = STATUS[c.status] || [c.status, ""];
      return "<tr><td>" + c.semester + "</td><td>" + esc(c.code + " — " + c.title) + '</td><td class="num">' + c.credit_hours + '</td><td><span class="status-pill ' + s[1] + '">' + esc(s[0]) + "</span></td></tr>";
    }).join("") : '<tr><td colspan="4">Your program’s course list is not set up yet. Ask the office.</td></tr>';
  }

  document.addEventListener("vistadmin:ready", audit);       // documents: my-documents.js
})();