(function () {
  "use strict";

  const VA = window.VistAdmin;

  function pct(part, total) {
    if (!total) return "0%";
    return Math.round((part / total) * 100) + "%";
  }

  function renderStats(data) {
    const counts = data.student_status_counts || {};
    const total = counts.total ?? 0;
    document.getElementById("stat-total").textContent = total;
    document.getElementById("stat-active").textContent = counts.active ?? 0;
    document.getElementById("stat-suspended").textContent = counts.suspended ?? 0;
    document.getElementById("stat-graduated").textContent = counts.graduated ?? 0;
    document.getElementById("stat-dropped").textContent = counts.dropped ?? 0;
    document.getElementById("stat-programs").textContent = data.total_programs ?? 0;

    // Percentage-of-total trends are genuinely derivable from what the API
    // already returns. A "+N this week" figure for Total Students is NOT
    // (the API has no created-in-last-7-days field) — left blank rather
    // than inventing a number.
    document.getElementById("stat-active-trend").textContent = pct(counts.active, total) + " of total";
    document.getElementById("stat-suspended-trend").textContent = pct(counts.suspended, total) + " of total";
    document.getElementById("stat-graduated-trend").textContent = pct(counts.graduated, total) + " of total";
    document.getElementById("stat-dropped-trend").textContent = pct(counts.dropped, total) + " of total";
  }

  function renderProgramsTable(data) {
    const tbody = document.getElementById("programs-tbody");
    const empty = document.getElementById("programs-empty");
    const rows = data.students_by_program || [];
    tbody.innerHTML = "";

    if (rows.length === 0) {
      empty.hidden = false;
      return;
    }
    empty.hidden = true;

    rows.forEach(function (row) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + VA.escapeHtml(row.program_name || "\u2014") + "</td>" +
        "<td>" + VA.escapeHtml(row.program_code || "\u2014") + "</td>" +
        "<td class=\"num\">" + VA.escapeHtml(row.student_count) + "</td>";
      tbody.appendChild(tr);
    });
  }

  function renderImportsTable(data) {
    const tbody = document.getElementById("imports-tbody");
    const empty = document.getElementById("imports-empty");
    const rows = data.recent_imports || [];
    tbody.innerHTML = "";

    if (rows.length === 0) {
      empty.hidden = false;
      return;
    }
    empty.hidden = true;

    rows.forEach(function (row) {
      const statusClass = "status-pill " + (row.status || "").toLowerCase();
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + VA.escapeHtml(row.filename) + "</td>" +
        "<td><span class=\"" + statusClass + "\">" + VA.escapeHtml(row.status) + "</span></td>" +
        "<td class=\"num\">" + VA.escapeHtml(row.imported_rows) + " / " + VA.escapeHtml(row.total_rows) + "</td>" +
        "<td>" + VA.escapeHtml(VA.formatDateTime(row.created_at)) + "</td>";
      tbody.appendChild(tr);
    });
  }

  function renderActivity(data) {
    const list = document.getElementById("activity-list");
    const empty = document.getElementById("activity-empty");
    const rows = data.recent_activity || [];
    list.innerHTML = "";

    if (rows.length === 0) {
      empty.hidden = false;
      return;
    }
    empty.hidden = true;

    rows.forEach(function (row) {
      const li = document.createElement("li");
      const actorText = row.actor_username ? VA.escapeHtml(row.actor_username) : "system";
      li.innerHTML =
        "<span class=\"activity-action\">" + VA.escapeHtml(row.action) + " \u2014 " +
        VA.escapeHtml(row.entity_type) + " (" + actorText + ")</span>" +
        "<span class=\"activity-meta\">" + VA.escapeHtml(VA.formatDateTime(row.created_at)) + "</span>";
      list.appendChild(li);
    });
  }

  async function loadDashboard() {
    document.getElementById("programs-tbody").innerHTML = VA.loadingRowHtml(3);
    document.getElementById("imports-tbody").innerHTML = VA.loadingRowHtml(4);
    document.getElementById("activity-list").innerHTML =
      '<li class="loading-cell"><div class="progress-ring small"></div><span>Loading&hellip;</span></li>';

    const result = await VA.fetchJson("/api/v1/reports/admin-dashboard");

    if (!result.ok) {
      const message =
        (result.body && result.body.error && result.body.error.message) ||
        "Could not load dashboard data. Please refresh the page.";
      VA.showError(message);
      return;
    }

    renderStats(result.body);
    renderProgramsTable(result.body);
    renderImportsTable(result.body);
    renderActivity(result.body);
  }

  document.addEventListener("vistadmin:ready", function () {
    loadDashboard();
  });
})();