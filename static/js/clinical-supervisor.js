(function () {
  "use strict";
  // Clinical supervisor: API /api/v1/clinical/supervise
  const VA = window.VistAdmin;
  const API = "/api/v1/clinical";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const day = function (v) { return new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }); };
  const iso = function (d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); };
  const NAMES = { SUBMITTED: "Waiting", SIGNED: "Signed", REJECTED: "Not signed" };
  let current = null;

  async function load() {
    const r = await VA.fetchJson(API + "/supervise");
    $("cv-rots").innerHTML = (r.ok ? r.body : []).map(function (x) {
      return '<tr data-id="' + x.id + '" data-name="' + esc(x.group + " · " + x.unit) + '" data-from="' + x.start_date + '" data-to="' + x.end_date + '"><td><strong>' + esc(day(x.start_date)) + "</strong><small>to " + esc(day(x.end_date)) +
        "</small></td><td>" + esc(x.group) + "<small>" + x.students + " students</small></td><td>" + esc(x.unit) + "<small>" + esc(x.site) + "</small></td><td>" + esc(x.days_text) +
        "<small>" + x.start_time + "–" + x.end_time + "</small></td><td>" + (x.to_sign || "—") + '</td><td><button type="button" class="row-action cv-open">Open</button></td></tr>';
    }).join("") || '<tr><td colspan="6" class="sd-dim">No rotation is given to you.</td></tr>';
  }

  async function sheet() {
    const r = await VA.fetchJson(API + "/supervise/" + current + "/day?day=" + $("cv-day").value);
    if (!r.ok) { VA.toast(err(r, "Could not load."), "error"); return; }
    $("cv-att-hint").textContent = r.body.is_rotation_day ? "" : "This day is not one of the rotation's days.";
    $("cv-att-save").disabled = !r.body.is_rotation_day;
    $("cv-att-body").innerHTML = r.body.students.map(function (s) {
      const st = s.status || "PRESENT";
      if (s.suspended) return '<tr class="att-locked"><td>' + esc(s.roll_number) + "</td><td>" + esc(s.name) + ' <small>suspended: not marked</small></td><td colspan="3"><span class="badge badge-locked">Suspended</span></td></tr>';
      return '<tr data-id="' + s.id + '"><td>' + esc(s.roll_number) + "</td><td>" + esc(s.name) + "</td>" + ["PRESENT", "ABSENT", "LEAVE"].map(function (v) {
        return '<td><input type="radio" name="a-' + s.id + '" value="' + v + '"' + (st === v ? " checked" : "") + ' aria-label="' + v.toLowerCase() + '"></td>';
      }).join("") + "</tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">No students in this group.</td></tr>';
  }

  async function logbook() {
    const r = await VA.fetchJson(API + "/supervise/" + current + "/logbook");
    $("cv-log-body").innerHTML = (r.ok ? r.body : []).map(function (x) {
      return '<tr data-id="' + x.id + '"><td>' + esc(day(x.day)) + "</td><td>" + esc(x.student) + "<small>" + esc(x.roll_number) + "</small></td><td>" + esc(x.skill) + (x.note ? "<small>" + esc(x.note) + "</small>" : "") +
        "</td><td>" + x.times + "</td><td>" + NAMES[x.status] + (x.reject_note ? "<small>" + esc(x.reject_note) + "</small>" : "") + "</td><td>" +
        (x.status === "SUBMITTED" ? '<button type="button" class="row-action cv-sign">Sign</button> <button type="button" class="row-action cv-reject">Not signed…</button>' : "") + "</td></tr>";
    }).join("") || '<tr><td colspan="6" class="sd-dim">No logbook entries yet.</td></tr>';
  }

  $("cv-rots").addEventListener("click", function (e) {
    const tr = e.target.closest(".cv-open") && e.target.closest("tr");
    if (!tr) return;
    current = tr.getAttribute("data-id");
    $("cv-att-title").textContent = tr.getAttribute("data-name");
    const today = iso(new Date()), from = tr.getAttribute("data-from"), to = tr.getAttribute("data-to");
    $("cv-day").min = from; $("cv-day").max = to < today ? to : today;
    $("cv-day").value = today < from ? from : (today > to ? to : today);
    $("cv-att").hidden = false; $("cv-log").hidden = false;
    sheet(); logbook();
  });
  $("cv-day").addEventListener("change", sheet);
  $("cv-att-save").addEventListener("click", async function () {
    const entries = Array.prototype.map.call($("cv-att-body").querySelectorAll("tr[data-id]"), function (tr) {
      const c = tr.querySelector("input:checked");
      return { student_id: tr.getAttribute("data-id"), status: c ? c.value : "PRESENT" };
    });
    const r = await VA.fetchJson(API + "/supervise/" + current + "/attendance", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ day: $("cv-day").value, entries: entries }) });
    if (!r.ok) { VA.toast(err(r, "Not saved."), "error"); return; }
    VA.toast("Attendance saved.");
  });
  $("cv-log-body").addEventListener("click", async function (e) {
    const ok = e.target.closest(".cv-sign"), no = e.target.closest(".cv-reject");
    if (!ok && !no) return;
    let note = null;
    if (no) { note = prompt("Why is it not signed? (the student sees this)"); if (!note) return; }
    const r = await VA.fetchJson(API + "/logbook/" + e.target.closest("tr").getAttribute("data-id") + "/sign", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approve: !!ok, note: note }) });
    if (!r.ok) { VA.toast(err(r, "Not saved."), "error"); return; }
    logbook(); load();
  });
  load();
})();
