(function () {
  "use strict";

  // Teacher progress: topics covered against the sessions due so far, most-behind first.
  // API: /api/v1/academics/teacher-progress
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const LABEL = { ON_TRACK: "On track", SLIGHTLY_DELAYED: "A little behind", DELAYED: "Behind", NOT_STARTED: "Not started" };
  const TONE = { ON_TRACK: "#16a34a", SLIGHTLY_DELAYED: "#f59e0b", DELAYED: "#dc2626", NOT_STARTED: "#94a3b8" };
  const ORDER = { DELAYED: 0, SLIGHTLY_DELAYED: 1, NOT_STARTED: 2, ON_TRACK: 3 };
  let rows = [];

  function pill(status) {
    return '<span class="status-pill ' + esc(String(status || "").toLowerCase()) + '">' + esc(LABEL[status] || String(status || "").replace(/_/g, " ")) + "</span>";
  }

  function render() {
    const q = document.getElementById("progress-q").value.trim().toLowerCase();
    const term = document.getElementById("progress-term").value;
    const status = document.getElementById("progress-status").value;
    const shown = rows.filter(function (e) {
      if (term && e.term_label !== term) return false;
      if (status && e.status !== status) return false;
      if (q && [e.teacher_name, e.employee_code, e.course_code, e.course_title].join(" ").toLowerCase().indexOf(q) === -1) return false;
      return true;
    });
    document.getElementById("progress-count").textContent = shown.length + " of " + rows.length + " course" + (rows.length === 1 ? "" : "s");
    document.getElementById("progress-tbody").innerHTML = shown.map(function (e) {
      const pct = Math.max(0, Math.min(100, Number(e.completion_percentage) || 0));
      return '<tr><td><span class="crs-code">' + esc(e.course_code) + "</span> " + esc(e.course_title) + "</td>" +
        "<td><strong>" + esc(e.teacher_name) + '</strong><small class="sd-dim tp-code">' + esc(e.employee_code) + "</small></td>" +
        "<td>" + esc(e.term_label) + "</td>" +
        '<td><div class="tp-cell"><div class="tp-bar"><span class="tp-meter"><i style="width:' + pct + "%;background:" + (TONE[e.status] || TONE.NOT_STARTED) + '"></i></span><b>' + Math.round(pct) + "%</b></div>" +
        '<small class="sd-dim">' + e.sessions_completed + " of " + e.sessions_due + " due so far</small></div></td>" +
        '<td class="num">' + (e.sessions_missing ? "<strong>" + e.sessions_missing + "</strong>" : '<span class="sd-dim">0</span>') + "</td>" +
        "<td>" + pill(e.status) + "</td></tr>";
    }).join("") || (rows.length ? '<tr><td colspan="6" class="sd-dim">Nothing matches.</td></tr>' : "");
  }

  async function loadProgress() {
    const result = await VA.fetchJson("/api/v1/academics/teacher-progress");
    if (!result.ok) { VA.showError("Could not load teacher progress."); return; }
    rows = result.body.slice().sort(function (a, b) {
      return (ORDER[a.status] ?? 9) - (ORDER[b.status] ?? 9) || (Number(a.completion_percentage) - Number(b.completion_percentage));
    });
    document.getElementById("progress-empty").hidden = rows.length > 0;
    document.getElementById("progress-table").hidden = rows.length === 0;
    const terms = Array.from(new Set(rows.map(function (e) { return e.term_label; })));
    document.getElementById("progress-term").innerHTML = '<option value="">All terms</option>' + terms.map(function (t) {
      return '<option value="' + esc(t) + '">' + esc(t) + "</option>";
    }).join("");
    const counts = {};
    rows.forEach(function (e) { counts[e.status] = (counts[e.status] || 0) + 1; });
    document.getElementById("progress-summary").innerHTML = ["DELAYED", "SLIGHTLY_DELAYED", "ON_TRACK", "NOT_STARTED"].map(function (s) {
      return '<button type="button" class="crs-chip tp-chip" data-status="' + s + '"><i style="background:' + TONE[s] + '"></i><b>' + (counts[s] || 0) + "</b> " + LABEL[s] + "</button>";
    }).join("");
    render();
  }

  document.getElementById("progress-q").addEventListener("input", render);
  document.getElementById("progress-term").addEventListener("change", render);
  document.getElementById("progress-status").addEventListener("change", render);
  document.getElementById("progress-summary").addEventListener("click", function (e) {
    const b = e.target.closest("[data-status]");
    if (!b) return;
    const sel = document.getElementById("progress-status");
    sel.value = sel.value === b.dataset.status ? "" : b.dataset.status;
    render();
  });

  document.addEventListener("vistadmin:ready", loadProgress);
})();