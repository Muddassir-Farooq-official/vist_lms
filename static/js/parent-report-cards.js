/* Parent -> Report cards (School mode): GET /api/v1/school/students/{id}/report-cards. */
(function () {
  "use strict";
  const P = window.ParentChild;
  P.onChange = async function (id) {
    const r = await window.VistAdmin.fetchJson("/api/v1/school/students/" + encodeURIComponent(id) + "/report-cards");
    if (!r.ok) { P.error("Could not load the report cards. Please refresh the page."); return; }
    document.getElementById("prc-view").hidden = false;
    window.VistCards(document.getElementById("prc-cards"), r.body);
  };
})();
