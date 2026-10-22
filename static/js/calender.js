(function () {
  "use strict";
  // Academic calendar: everyone reads it; the administration adds and removes entries.
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const KIND = { HOLIDAY: "Holiday", EXAM: "Exams", EVENT: "Event", TERM: "Term date", DEADLINE: "Deadline" };
  const PILL = { HOLIDAY: "active", EXAM: "pending", DEADLINE: "dropped", EVENT: "", TERM: "" };
  let canManage = false;
  const fmt = function (iso) { return new Date(String(iso).slice(0, 10) + "T00:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" }); };

  async function load() {
    const m = $("cal-month").value;
    const start = (m || new Date().toISOString().slice(0, 7)) + "-01";
    const end = new Date(start + "T00:00:00"); end.setMonth(end.getMonth() + 6);
    const iso = end.getFullYear() + "-" + String(end.getMonth() + 1).padStart(2, "0") + "-01";
    const tb = $("cal-table").tBodies[0];
    const r = await VA.fetchJson("/api/v1/calendar?start=" + start + "&end=" + iso);
    if (!r.ok) {
      tb.innerHTML = '<tr><td colspan="5">' + esc((r.body && r.body.error && r.body.error.message) || "Could not load the calendar.") + ' <button type="button" class="link-btn" id="cal-retry">Try again</button></td></tr>';
      $("cal-retry").addEventListener("click", load);
      return;
    }
    $("cal-empty").hidden = r.body.length > 0;
    tb.innerHTML = "";
    r.body.forEach(function (e) {
      const tr = document.createElement("tr");
      const when = e.start_date === e.end_date ? fmt(e.start_date) : fmt(e.start_date) + " – " + fmt(e.end_date) + " (" + e.days + " days)";
      const who = [e.for_students ? "Students" : "", e.for_staff ? "Staff" : ""].filter(Boolean).join(", ");
      tr.innerHTML = "<td>" + esc(when) + "</td><td><strong>" + esc(e.title) + "</strong>" + (e.details ? "<small>" + esc(e.details) + "</small>" : "") +
        '</td><td><span class="status-pill ' + (PILL[e.kind] || "") + '">' + esc(KIND[e.kind] || e.kind) + "</span></td><td>" + esc(who) + '</td><td class="row-actions"></td>';
      if (canManage) {
        const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn secondary-btn"; b.textContent = "Remove";
        b.addEventListener("click", async function () {
          if (!(await VA.confirm("Remove “" + e.title + "” from the calendar? Classes already cancelled and staff days already excused stay as they are."))) return;
          const x = await VA.fetchJson("/api/v1/calendar/" + e.id, { method: "DELETE" });
          if (!x.ok) return VA.toast((x.body && x.body.error && x.body.error.message) || "Could not remove it.", "error");
          VA.toast("Removed."); load();
        });
        tr.querySelector(".row-actions").appendChild(b);
      }
      tb.appendChild(tr);
    });
  }

  $("cal-start").addEventListener("change", function () { if (!$("cal-end").value || $("cal-end").value < this.value) $("cal-end").value = this.value; });
  $("cal-form").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    const err = $("cal-error"); err.hidden = true; $("cal-done").hidden = true;
    const body = { title: $("cal-title").value.trim(), kind: $("cal-kind").value, start_date: $("cal-start").value, end_date: $("cal-end").value || $("cal-start").value,
      details: $("cal-details").value.trim() || null, for_students: $("cal-students").checked, for_staff: $("cal-staff").checked };
    if (body.title.length < 3) { err.textContent = "Give it a title."; err.hidden = false; return; }
    if (!body.start_date) { err.textContent = "Choose the first day."; err.hidden = false; return; }
    if (body.kind === "HOLIDAY" && !(await VA.confirm("This holiday cancels " + (body.for_students ? "classes" : "") + (body.for_students && body.for_staff ? " and " : "") + (body.for_staff ? "excuses staff attendance" : "") + " on those days. Continue?"))) return;
    const r = await VA.fetchJson("/api/v1/calendar", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { err.textContent = (r.body && r.body.error && r.body.error.message) || "Could not add it."; err.hidden = false; return; }
    const fx = r.body.effects || {};
    $("cal-done").textContent = "Added." + (fx.classes_cancelled ? " " + fx.classes_cancelled + " class(es) cancelled." : "") + (fx.staff_days_excused ? " " + fx.staff_days_excused + " staff day(s) excused." : "");
    $("cal-done").hidden = false;
    ["cal-title", "cal-details"].forEach(function (id) { $(id).value = ""; });
    load();
  });

  document.addEventListener("vistadmin:ready", function (ev) {
    const perms = (ev.detail && ev.detail.me && ev.detail.me.permissions) || [];
    canManage = perms.indexOf("calendar.manage") !== -1;
    $("cal-manage").hidden = !canManage;
    const now = new Date();
    $("cal-month").value = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0");
    $("cal-month").addEventListener("change", load);
    load();
  });
})();