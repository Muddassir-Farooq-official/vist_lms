/* Admin -> Classes & sections -> Parent-teacher meetings: set a day (slots for every class teacher), see bookings, cancel.
   API: GET/POST /api/v1/school/ptm, POST /ptm/{id}/cancel. */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (s) { return VA.escapeHtml(s == null ? "" : String(s)); };
  function msg(r, f) { return (r.body && r.body.error && r.body.error.message) || f; }

  async function load() {
    const r = await VA.fetchJson("/api/v1/school/ptm");
    const box = $("pm-list");
    if (!r.ok) { box.innerHTML = '<p class="dash-alert error">' + esc(msg(r, "Could not load.")) + "</p>"; return; }
    if (!r.body.length) { box.innerHTML = '<p class="empty-state">No meeting day yet.</p>'; return; }
    box.innerHTML = r.body.map(function (d) {
      return '<div class="pm-day"><h4>' + esc(VA.formatDateLong(d.day)) + " · " + esc(d.start_time + "–" + d.end_time) + " · " + d.slot_minutes + " min each" +
        (d.cancelled ? ' <span class="status-pill dropped">Cancelled</span>' : "") + "</h4>" +
        (d.note ? '<p class="sc-hint">' + esc(d.note) + "</p>" : "") +
        '<p class="sc-hint">' + d.booked + " of " + d.slots + " times booked</p>" +
        '<table class="dash-table"><thead><tr><th>Class teacher</th><th class="num">Booked</th><th class="num">Times</th></tr></thead><tbody>' +
        d.teachers.map(function (t) { return "<tr><td>" + esc(t.name) + '</td><td class="num">' + t.booked + '</td><td class="num">' + t.slots + "</td></tr>"; }).join("") +
        "</tbody></table>" + (d.cancelled ? "" : '<div class="toolbar"><button type="button" class="role-btn secondary-btn small-btn" data-cancel="' + esc(d.id) + '">Cancel this day</button></div>') + "</div>";
    }).join("");
    box.querySelectorAll("[data-cancel]").forEach(function (b) {
      b.addEventListener("click", async function () {
        if (!(await VA.confirm("Cancel this meeting day? Parents who booked are told.", "Cancel day"))) return;
        const x = await VA.fetchJson("/api/v1/school/ptm/" + b.dataset.cancel + "/cancel", { method: "POST" });
        if (!x.ok) { VA.toast(msg(x, "Could not cancel."), "error"); return; }
        VA.toast("Meeting day cancelled.", "success");
        load();
      });
    });
  }

  async function create(e) {
    e.preventDefault();
    const err = $("pm-error"); err.hidden = true;
    if (!$("pm-day").value) { err.textContent = "Choose the day."; err.hidden = false; return; }
    $("pm-go").disabled = true;
    const r = await VA.fetchJson("/api/v1/school/ptm", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ day: $("pm-day").value, start_time: $("pm-start").value, end_time: $("pm-end").value,
        slot_minutes: Number($("pm-mins").value || 10), note: $("pm-note").value.trim() || null }) });
    $("pm-go").disabled = false;
    if (!r.ok) { err.textContent = msg(r, "Could not set the meeting day."); err.hidden = false; return; }
    VA.toast("Meeting day set: " + r.body.slots + " times for " + r.body.teachers.length + " class teachers. Parents have been told.", "success");
    $("pm-note").value = "";
    load();
  }

  window.VistPtm = { load: load };
  document.addEventListener("vistadmin:ready", function () { $("pm-form").addEventListener("submit", create); });
})();
