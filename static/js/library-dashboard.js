(function () {
  "use strict";

  const VA = window.VistAdmin;

  async function loadDashboard() {
    const result = await VA.fetchJson("/api/v1/library/dashboard");
    if (!result.ok) {
      VA.showError((result.body && result.body.error && result.body.error.message) || "Could not load dashboard.");
      return;
    }
    VA.clearError();

    const d = result.body;
    document.getElementById("stat-titles").textContent = d.total_titles;
    document.getElementById("stat-total-copies").textContent = d.total_copies;
    document.getElementById("stat-available").textContent = d.available_copies;
    document.getElementById("stat-issued").textContent = d.issued_copies;
    document.getElementById("stat-overdue").textContent = d.overdue_count;

    const issuesTbody = document.getElementById("recent-issues-tbody");
    const issuesEmpty = document.getElementById("recent-issues-empty");
    issuesTbody.innerHTML = "";
    if (d.recent_issues.length === 0) {
      issuesEmpty.hidden = false;
    } else {
      issuesEmpty.hidden = true;
      d.recent_issues.forEach(function (i) {
        const tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" + VA.escapeHtml(i.book_title) + "</td>" +
          "<td>" + VA.escapeHtml(i.borrower_name) + "</td>" +
          "<td>" + VA.escapeHtml(i.due_date) + "</td>";
        issuesTbody.appendChild(tr);
      });
    }

    const returnsTbody = document.getElementById("recent-returns-tbody");
    const returnsEmpty = document.getElementById("recent-returns-empty");
    returnsTbody.innerHTML = "";
    if (d.recent_returns.length === 0) {
      returnsEmpty.hidden = false;
    } else {
      returnsEmpty.hidden = true;
      d.recent_returns.forEach(function (r) {
        const tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" + VA.escapeHtml(r.book_title) + "</td>" +
          "<td>" + VA.escapeHtml(r.borrower_name) + "</td>" +
          "<td>" + VA.escapeHtml(r.returned_at || "\u2014") + "</td>";
        returnsTbody.appendChild(tr);
      });
    }
  }

  document.addEventListener("vistadmin:ready", function () {
    loadDashboard();
  });
})();