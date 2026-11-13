/* Student -> Report cards (School mode): GET /api/v1/school/me/report-cards. */
(function () {
  "use strict";
  document.addEventListener("vistadmin:ready", async function () {
    const r = await window.VistAdmin.fetchJson("/api/v1/school/me/report-cards");
    const box = document.getElementById("rc-cards");
    if (!r.ok) { box.textContent = "Could not load your report cards."; return; }
    window.VistCards(box, r.body);
  });
})();
