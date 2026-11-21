// Teacher > My Timetable > My available times (app/timetabling): the periods I cannot teach, used by the smart timetable.
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const API = "/api/v1/academics/timetable/smart/my-availability";
  const box = document.getElementById("my-availability");
  if (!box || !window.AvailabilityGrid) return;
  async function load() {
    const r = await VA.fetchJson(API, { quiet: true });
    if (!r.ok) { box.innerHTML = '<p class="sd-dim">' + VA.escapeHtml((r.body && r.body.error && r.body.error.message) || "Could not load your times.") + "</p>"; return; }
    window.AvailabilityGrid.render(box, r.body, {
      admin: false,
      onSave: function (payload) { return VA.fetchJson(API, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); },
    });
  }
  load();
})();
