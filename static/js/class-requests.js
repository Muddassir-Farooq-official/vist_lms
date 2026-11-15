(function () {
  "use strict";
  // Faculty -> My Timetable -> Free slots & extra classes: find a free period, ask for a make-up or extra class.
  const VA = window.VistAdmin;
  const API = "/api/v1/academics/class-requests";
  const $ = function (id) { return document.getElementById(id); };
  if (!$("cr-course")) return;
  let courses = [], missed = [];

  function esc(v) { return VA.escapeHtml(v == null ? "" : String(v)); }
  function errText(body, fallback) { return (body && body.error && body.error.message) || fallback; }
  function fmtDate(iso) {
    try { return new Date(iso + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "2-digit", month: "short" }); } catch (e) { return iso; }
  }

  function course() { return courses.find(function (c) { return c.id === $("cr-course").value; }); }

  function noteForCourse() {
    const c = course();
    if (!c) { $("cr-course-note").textContent = ""; return; }
    const bits = [];
    if (c.term_start_date && c.term_end_date) bits.push("Term " + fmtDate(c.term_start_date) + " – " + fmtDate(c.term_end_date));
    bits.push(c.missed_classes + " missed class" + (c.missed_classes === 1 ? "" : "es"));
    if (c.sessions_missing) bits.push(c.sessions_missing + " topic" + (c.sessions_missing === 1 ? "" : "s") + " behind");
    $("cr-course-note").textContent = bits.join(" · ");
  }

  async function loadMissed() {
    const c = course();
    missed = [];
    $("cr-missed").innerHTML = "";
    if (!c) return;
    const r = await VA.fetchJson(API + "/offerings/" + c.id + "/missed");
    if (r.ok) missed = r.body;
    $("cr-missed").innerHTML = missed.length ? missed.map(function (m) {
      return '<option value="' + esc(m.id) + '">' + esc(fmtDate(m.date) + " " + m.start_time + " — " + m.why) + "</option>";
    }).join("") : '<option value="">No missed classes</option>';
  }

  function kindChanged() { $("cr-missed-wrap").hidden = $("cr-kind").value !== "MAKEUP"; }

  async function loadSlots() {
    const c = course(), box = $("cr-slots");
    if (!c) { box.innerHTML = '<p class="cc-meta">You have no current courses.</p>'; return; }
    box.innerHTML = '<p class="cc-meta">Looking for free periods…</p>';
    const q = new URLSearchParams({ days: $("cr-days").value });
    if ($("cr-from").value) q.set("start", $("cr-from").value);
    const r = await VA.fetchJson(API + "/offerings/" + c.id + "/free-slots?" + q.toString());
    if (!r.ok) { box.innerHTML = '<p class="cc-meta">' + esc(errText(r.body, "Could not load free periods.")) + "</p>"; return; }
    if (!r.body.length) { box.innerHTML = '<p class="cc-meta">No free period for you, this class and a room in these days. Try later days.</p>'; return; }
    const byDay = {};
    r.body.forEach(function (s) { (byDay[s.date] = byDay[s.date] || []).push(s); });
    box.innerHTML = Object.keys(byDay).sort().map(function (d) {
      return '<div class="cr-day"><h3>' + esc(fmtDate(d)) + '</h3><div class="cr-day-row">' + byDay[d].map(function (s) {
        return '<div class="cr-slot"><strong>Period ' + esc(s.period_number) + " · " + esc(s.start_time) + "–" + esc(s.end_time) + "</strong>" +
          '<div class="cr-row"><select aria-label="Room">' + s.rooms.map(function (room) { return "<option>" + esc(room) + "</option>"; }).join("") + "</select>" +
          '<button type="button" class="role-btn cr-book" data-date="' + esc(s.date) + '" data-period="' + esc(s.period_id) + '">Book</button></div></div>';
      }).join("") + "</div></div>";
    }).join("");
    box.querySelectorAll(".cr-book").forEach(function (btn) {
      btn.addEventListener("click", function () { book(btn); });
    });
  }

  async function book(btn) {
    const c = course(), kind = $("cr-kind").value;
    const room = btn.parentElement.querySelector("select").value;
    const body = { kind: kind, session_date: btn.getAttribute("data-date"), period_id: btn.getAttribute("data-period"), room: room };
    if (kind === "MAKEUP") {
      if (!$("cr-missed").value) return VA.toast("Choose the missed class first (or book an extra class instead).", "error");
      body.replaces_session_id = $("cr-missed").value;
      const why = await VA.ask({ title: "Make-up class", intro: "Anything the administrator should know? (optional)", label: "Note", value: "", required: false, ok: "Send for approval" });
      if (why == null) return;
      body.reason = why.trim() || null;
    } else {
      const why = await VA.ask({ title: "Extra class", intro: "Why is the extra class needed? For example: two topics behind before the mid-term.", label: "Reason", value: "", min: 5, ok: "Send for approval" });
      if (why == null) return;
      body.reason = why.trim();
    }
    btn.disabled = true;
    const r = await VA.fetchJson(API + "/offerings/" + c.id, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    btn.disabled = false;
    if (!r.ok) { VA.toast(errText(r.body, "Could not book that period."), "error"); loadSlots(); return; }
    VA.toast(r.body.message || "Sent for approval.");
    await Promise.all([loadMissed(), loadSlots(), loadMine()]);
  }

  async function loadMine() {
    const r = await VA.fetchJson(API + "/mine", { quiet: true });
    const box = $("cr-mine");
    if (!r.ok) { box.innerHTML = '<p class="cc-meta">Could not load your requests.</p>'; return; }
    if (!r.body.length) { box.innerHTML = '<p class="cc-meta">No requests yet.</p>'; return; }
    const label = { REQUESTED: "Waiting", APPROVED: "Approved", REJECTED: "Rejected", CANCELLED: "Withdrawn" };
    box.innerHTML = '<div class="table-wrap"><table class="data-table"><thead><tr><th>Class</th><th>Status</th><th></th></tr></thead><tbody>' +
      r.body.map(function (x) {
        return "<tr><td>" + esc(x.summary) + (x.reason ? '<div class="cc-meta">' + esc(x.reason) + "</div>" : "") +
          (x.decision_note ? '<div class="cc-meta">Admin: ' + esc(x.decision_note) + "</div>" : "") + "</td>" +
          '<td><span class="cr-status ' + esc(x.status) + '">' + esc(label[x.status] || x.status) + "</span></td>" +
          "<td>" + (x.status === "REQUESTED" ? '<button type="button" class="row-action cr-withdraw" data-id="' + esc(x.id) + '">Withdraw</button>' : "") + "</td></tr>";
      }).join("") + "</tbody></table></div>";
    box.querySelectorAll(".cr-withdraw").forEach(function (b) {
      b.addEventListener("click", async function () {
        if (!(await VA.confirm("Withdraw this request? The slot is freed for others."))) return;
        const res = await VA.fetchJson("/api/v1/approvals/" + b.getAttribute("data-id") + "/cancel", { method: "POST" });
        if (!res.ok) return VA.toast(errText(res.body, "Could not withdraw it."), "error");
        VA.toast("Withdrawn.");
        await Promise.all([loadMissed(), loadSlots(), loadMine()]);
      });
    });
  }

  async function init() {
    const r = await VA.fetchJson(API + "/my-courses", { quiet: true });
    courses = r.ok ? r.body : [];
    $("cr-course").innerHTML = courses.length ? courses.map(function (c) {
      return '<option value="' + esc(c.id) + '">' + esc(c.label + " — " + c.title + (c.term_label ? " (" + c.term_label + ")" : "")) + "</option>";
    }).join("") : '<option value="">No current courses</option>';
    noteForCourse();
    kindChanged();
    await Promise.all([loadMissed(), loadSlots(), loadMine()]);
  }

  $("cr-course").addEventListener("change", function () { noteForCourse(); loadMissed(); loadSlots(); });
  $("cr-kind").addEventListener("change", kindChanged);
  $("cr-from").addEventListener("change", loadSlots);
  $("cr-days").addEventListener("change", loadSlots);

  let started = false;      // load only when the tab is first opened
  document.querySelectorAll('.portal-tab[data-tab="extra"]').forEach(function (t) {
    t.addEventListener("click", function () { if (!started) { started = true; init(); } });
  });
  if (location.hash === "#extra") { started = true; init(); }
})();
