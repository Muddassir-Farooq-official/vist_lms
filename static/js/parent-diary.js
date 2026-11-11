/* Parent -> Diary & attendance (School mode): /api/v1/school/students/{id}/homework and /days. */
(function () {
  "use strict";
  const P = window.ParentChild, VA = window.VistAdmin, D = window.VistDiary;
  const $ = function (id) { return document.getElementById(id); };
  let child = null;
  async function days(month) {
    const r = await VA.fetchJson("/api/v1/school/students/" + encodeURIComponent(child) + "/days" + (month ? "?month=" + month : ""));
    if (r.ok) D.month($("pd-at"), r.body, days); else $("pd-at").textContent = "Could not load the attendance.";
  }
  P.onChange = async function (id) {
    child = id;
    const r = await VA.fetchJson("/api/v1/school/students/" + encodeURIComponent(id) + "/homework");
    if (!r.ok) { P.error("Could not load the diary. Please refresh the page."); return; }
    $("pd-view").hidden = false;
    D.homework($("pd-hw"), r.body);
    days();
  };
})();
