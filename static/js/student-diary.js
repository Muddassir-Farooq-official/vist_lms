/* Student -> Diary & attendance (School mode): /api/v1/school/me/homework and /me/days. */
(function () {
  "use strict";
  const VA = window.VistAdmin, D = window.VistDiary;
  const $ = function (id) { return document.getElementById(id); };
  async function days(month) {
    const r = await VA.fetchJson("/api/v1/school/me/days" + (month ? "?month=" + month : ""));
    if (r.ok) D.month($("sd-at"), r.body, days); else $("sd-at").textContent = "Could not load your attendance.";
  }
  document.addEventListener("vistadmin:ready", async function () {
    const r = await VA.fetchJson("/api/v1/school/me/homework");
    if (r.ok) D.homework($("sd-hw"), r.body); else $("sd-hw").textContent = "Could not load the diary.";
    days();
  });
})();
