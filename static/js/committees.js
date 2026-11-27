(function () {
  "use strict";
  // Committees: API /api/v1/committees
  const VA = window.VistAdmin;
  const API = "/api/v1/committees";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const send = function (url, method, body) { return VA.fetchJson(url, { method: method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }); };
  const day = function (v) { return v ? new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—"; };
  const STATUS = { SCHEDULED: "Scheduled", FINAL: "Minutes final", CANCELLED: "Cancelled", OPEN: "Open", DONE: "Done", DROPPED: "Dropped" };
  const admin = !!$("cm-form");
  let list = [], committee = null, meeting = null, editing = null;

  async function mine() {
    const r = await VA.fetchJson(API + "/actions/mine");
    $("cm-mine").innerHTML = (r.ok ? r.body : []).map(function (a) {
      return '<tr data-id="' + a.id + '"><td>' + esc(a.title) + (a.done_note ? "<small>" + esc(a.done_note) + "</small>" : "") + "</td><td>" + esc(a.committee) + "</td><td>" + (a.overdue ? "<b>" + esc(day(a.due_on)) + " (overdue)</b>" : esc(day(a.due_on))) +
        "</td><td>" + STATUS[a.status] + "</td><td>" + (a.status === "OPEN" ? '<button type="button" class="row-action cm-done">Done…</button>' : "") + "</td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">No actions given to you.</td></tr>';
  }
  async function openAll() {
    if (!admin) return;
    const r = await VA.fetchJson(API + "/actions/open");
    $("cm-open").innerHTML = (r.ok ? r.body : []).map(function (a) {
      return "<tr><td>" + esc(a.title) + "</td><td>" + esc(a.committee) + "</td><td>" + esc(a.owner) + "</td><td>" + (a.overdue ? "<b>" + esc(day(a.due_on)) + "</b>" : esc(day(a.due_on))) + "</td></tr>";
    }).join("") || '<tr><td colspan="4" class="sd-dim">No open actions.</td></tr>';
  }
  async function load() {
    const r = await VA.fetchJson(API);
    list = r.ok ? r.body : [];
    $("cm-list").innerHTML = list.map(function (c) {
      return '<article class="sh-card" data-id="' + c.id + '"><h3>' + esc(c.name) + (c.is_active ? "" : " (closed)") + (c.my_role ? " <small>you: " + esc(c.members.find(function (m) { return m.role === c.my_role; }).role_name) + "</small>" : "") + "</h3>" +
        (c.purpose ? "<p>" + esc(c.purpose) + "</p>" : "") + '<p class="panel-hint">' + c.members.map(function (m) { return esc(m.name) + (m.role !== "MEMBER" ? " (" + esc(m.role_name) + ")" : "") + (m.away ? " <em>(away)</em>" : ""); }).join(", ") +
        (c.next_meeting ? " · next meeting " + esc(day(c.next_meeting)) : "") + '</p><div class="decision-bar"><button type="button" class="role-btn secondary-btn cm-open">Meetings</button>' +
        (admin ? ' <button type="button" class="role-btn secondary-btn cm-edit">Change</button>' : "") + "</div></article>";
    }).join("") || '<p class="sd-dim">You are not on any committee.</p>';
  }
  async function meetings(id) {
    committee = list.find(function (c) { return c.id === id; });
    $("cm-d-title").textContent = committee.name + " — meetings";
    $("cm-mt-form").hidden = !committee.can_run || !committee.is_active;
    const r = await VA.fetchJson(API + "/" + id + "/meetings");
    $("cm-meetings").innerHTML = (r.ok ? r.body : []).map(function (m) {
      return '<tr data-id="' + m.id + '"><td>' + esc(day(m.held_on)) + (m.start_time ? " " + esc(m.start_time) : "") + "</td><td>" + esc(m.place || "—") + "</td><td>" + STATUS[m.status] + "</td><td>" + (m.open_actions || "—") +
        '</td><td><button type="button" class="row-action cm-mt-open">Open</button></td></tr>';
    }).join("") || '<tr><td colspan="5" class="sd-dim">No meetings yet.</td></tr>';
    $("cm-detail").hidden = false; $("cm-meeting").hidden = true;
  }
  async function openMeeting(id) {
    const r = await VA.fetchJson(API + "/meetings/" + id);
    if (!r.ok) return VA.toast(err(r, "Could not open."), "error");
    meeting = r.body;
    $("cm-m-title").textContent = meeting.committee + " — " + day(meeting.held_on) + " (" + STATUS[meeting.status] + ")";
    const f = $("cm-min-form");
    ["agenda", "attended", "minutes", "decisions"].forEach(function (k) { f.elements[k].value = meeting[k] || ""; f.elements[k].readOnly = !meeting.can_edit; });
    $("cm-min-bar").hidden = !meeting.can_edit;
    $("cm-final").hidden = !meeting.can_finalise;
    $("cm-pdf").href = API + "/meetings/" + meeting.id + "/pdf";
    $("cm-act-form").hidden = !(committee && committee.can_run) || meeting.status === "CANCELLED";
    $("cm-actions").innerHTML = meeting.actions.map(function (a) {
      const btns = a.status === "OPEN" && committee && committee.can_run ? '<button type="button" class="row-action cm-a-done">Done</button> <button type="button" class="row-action cm-a-drop">Drop</button>' : "";
      return '<tr data-id="' + a.id + '"><td>' + esc(a.title) + (a.done_note ? "<small>" + esc(a.done_note) + "</small>" : "") + "</td><td>" + esc(a.owner) + "</td><td>" + (a.overdue ? "<b>" + esc(day(a.due_on)) + "</b>" : esc(day(a.due_on))) + "</td><td>" + STATUS[a.status] + "</td><td>" + btns + "</td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">No actions.</td></tr>';
    $("cm-meeting").hidden = false;
    $("cm-meeting").scrollIntoView({ behavior: "smooth" });
  }
  async function actionTo(id, status, note) {
    const r = await send(API + "/actions/" + id, "POST", { status: status, note: note || null });
    if (!r.ok) return VA.toast(err(r, "Not saved."), "error");
    mine(); openAll(); if (meeting) openMeeting(meeting.id);
  }
  $("cm-mine").addEventListener("click", async function (e) {
    if (!e.target.closest(".cm-done")) return;
    const note = await VA.ask({ title: "Action done", label: "What was done (optional)", min: 0 });
    if (note === null) return;
    actionTo(e.target.closest("tr").getAttribute("data-id"), "DONE", note);
  });
  $("cm-list").addEventListener("click", function (e) {
    const card = e.target.closest("[data-id]");
    if (!card) return;
    if (e.target.closest(".cm-open")) meetings(card.getAttribute("data-id"));
    if (e.target.closest(".cm-edit")) {
      const c = list.find(function (x) { return x.id === card.getAttribute("data-id"); });
      editing = c.id;
      $("cm-form-title").textContent = "Change " + c.name; $("cm-cancel").hidden = false;
      $("cm-name").value = c.name; $("cm-purpose").value = c.purpose || "";
      $("cm-members").value = c.members.map(function (m) { return (m.employee_code || "") + " " + m.role.toLowerCase(); }).join("\n");
      $("cm-name").focus();
    }
  });
  $("cm-meetings").addEventListener("click", function (e) { if (e.target.closest(".cm-mt-open")) openMeeting(e.target.closest("tr").getAttribute("data-id")); });
  $("cm-mt-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const r = await send(API + "/" + committee.id + "/meetings", "POST", { held_on: $("cm-mt-date").value, start_time: $("cm-mt-time").value || null, place: $("cm-mt-place").value || null, agenda: $("cm-mt-agenda").value });
    if (!r.ok) return VA.toast(err(r, "Not scheduled."), "error");
    VA.toast("Scheduled. Members are told."); $("cm-mt-form").reset(); meetings(committee.id); load();
  });
  $("cm-min-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const f = e.target, body = {};
    ["agenda", "attended", "minutes", "decisions"].forEach(function (k) { body[k] = f.elements[k].value; });
    const r = await send(API + "/meetings/" + meeting.id, "PUT", body);
    if (!r.ok) return VA.toast(err(r, "Not saved."), "error");
    VA.toast("Minutes saved.");
  });
  $("cm-final").addEventListener("click", async function () {
    if (!(await VA.confirm("Make these minutes final? They can no longer be changed. Save any edits first."))) return;
    const r = await send(API + "/meetings/" + meeting.id + "/final", "POST");
    if (!r.ok) return VA.toast(err(r, "Not done."), "error");
    openMeeting(meeting.id); meetings(committee.id);
  });
  $("cm-cancel-mt").addEventListener("click", async function () {
    if (!(await VA.confirm("Cancel this meeting?"))) return;
    const r = await send(API + "/meetings/" + meeting.id + "/cancel", "POST");
    if (!r.ok) return VA.toast(err(r, "Not done."), "error");
    openMeeting(meeting.id); meetings(committee.id);
  });
  $("cm-actions").addEventListener("click", function (e) {
    const tr = e.target.closest("tr");
    if (e.target.closest(".cm-a-done")) actionTo(tr.getAttribute("data-id"), "DONE");
    if (e.target.closest(".cm-a-drop")) actionTo(tr.getAttribute("data-id"), "DROPPED");
  });
  $("cm-act-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const r = await send(API + "/meetings/" + meeting.id + "/actions", "POST", { title: $("cm-act-title").value, owner_code: $("cm-act-owner").value.trim(), due_on: $("cm-act-due").value });
    if (!r.ok) return VA.toast(err(r, "Not saved."), "error");
    VA.toast("Action given. The owner is told."); $("cm-act-form").reset(); openMeeting(meeting.id); openAll();
  });
  if (admin) {
    $("cm-cancel").addEventListener("click", function () { editing = null; $("cm-form").reset(); $("cm-form-title").textContent = "Make a committee"; $("cm-cancel").hidden = true; });
    $("cm-form").addEventListener("submit", async function (e) {
      e.preventDefault();
      const members = $("cm-members").value.split("\n").map(function (l) { return l.trim(); }).filter(Boolean).map(function (l) {
        const parts = l.split(/[\s,;]+/);
        const role = (parts[1] || "member").toUpperCase();
        return { employee_code: parts[0], role: role === "CHAIR" || role === "SECRETARY" ? role : "MEMBER" };
      });
      const r = await send(API + (editing ? "/" + editing : ""), editing ? "PUT" : "POST", { name: $("cm-name").value, purpose: $("cm-purpose").value || null, members: members });
      if (!r.ok) return VA.toast(err(r, "Not saved."), "error");
      VA.toast("Saved."); $("cm-cancel").click(); load();
    });
  }
  mine(); openAll(); load();
})();
