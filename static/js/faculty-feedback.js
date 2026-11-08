/* Faculty -> Student feedback (GET /api/v1/feedback/teaching). */
(function () {
  "use strict";
  const VA = window.VistAdmin, FV = window.FeedbackView;
  async function load() {
    const list = document.getElementById("fb-list");
    const r = await VA.fetchJson("/api/v1/feedback/teaching");
    list.textContent = "";
    if (!r.ok) { list.appendChild(FV.el("p", "dash-alert error", (r.body && r.body.error && r.body.error.message) || "Could not load.")); return; }
    document.getElementById("fb-none").hidden = r.body.length > 0;
    r.body.forEach(function (row) {
      let reason = row.hidden_reason;
      if (!reason && !row.responses) reason = "No answers.";
      if (row.is_open) reason = "The survey is open until " + FV.day(row.closes_on) + ": " + row.responses + " of " + row.enrolled + " students have answered so far. " + (row.hidden_reason || "");
      list.appendChild(FV.card(row, reason));
    });
  }
  document.addEventListener("vistadmin:ready", load);
})();
