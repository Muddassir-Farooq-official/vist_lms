(function () {
  "use strict";

  const VA = window.VistAdmin;

  function statusPillHtml(status) {
    const label = String(status || "").replace(/_/g, " ");
    return '<span class="status-pill ' + String(status || "").toLowerCase() + '">' + VA.escapeHtml(label) + "</span>";
  }

  async function loadProgress() {
    const result = await VA.fetchJson("/api/v1/academics/teacher-progress");
    const tbody = document.getElementById("progress-tbody");
    const empty = document.getElementById("progress-empty");
    tbody.innerHTML = "";
    if (!result.ok) {
      VA.showError("Could not load teacher progress.");
      return;
    }

    empty.hidden = result.body.length > 0;
    result.body.forEach(function (entry) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + VA.escapeHtml(entry.teacher_name) + " (" + VA.escapeHtml(entry.employee_code) + ")</td>" +
        "<td>" + VA.escapeHtml(entry.course_code) + " \u2014 " + VA.escapeHtml(entry.course_title) + "</td>" +
        "<td>" + VA.escapeHtml(entry.term_label) + "</td>" +
        '<td class="num">' + entry.sessions_due + "</td>" +
        '<td class="num">' + entry.sessions_completed + "</td>" +
        '<td class="num">' + entry.sessions_missing + "</td>" +
        '<td class="num">' + entry.completion_percentage + "%</td>" +
        "<td>" + statusPillHtml(entry.status) + "</td>";
      tbody.appendChild(tr);
    });
  }

  document.addEventListener("vistadmin:ready", function () {
    loadProgress();
  });
})();