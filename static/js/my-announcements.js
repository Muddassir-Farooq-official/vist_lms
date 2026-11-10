/* Teachers & staff -> Post an announcement (/api/v1/announcements/mine): write, send for approval, fix a rejected one. */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/announcements/mine";
  const PILL = { PENDING: "pending", APPROVED: "active", REJECTED: "dropped", CANCELLED: "" };
  let data = null;
  let editing = null;          // the announcement being changed, or null for a new one

  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function day(d) { return d ? new Date(d).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : ""; }
  function err(r, f) { return (r.body && r.body.error && r.body.error.message) || f; }
  function chip(box, value, text, name) {
    const l = el("label");
    const c = document.createElement("input"); c.type = "checkbox"; c.value = value; c.name = name;
    l.appendChild(c); l.appendChild(document.createTextNode(" " + text));
    box.appendChild(l);
  }
  function ticked(id) { return Array.prototype.map.call(document.querySelectorAll("#" + id + " input:checked"), function (c) { return c.value; }); }
  function tick(id, values) {
    const set = new Set((values || []).map(String));
    document.querySelectorAll("#" + id + " input").forEach(function (c) { c.checked = set.has(c.value); });
  }
  function scope() { return (document.querySelector('input[name="an-scope"]:checked') || {}).value || "ALL"; }
  function showScope() {
    const s = scope();
    document.querySelectorAll(".an-scope").forEach(function (x) { x.hidden = x.getAttribute("data-scope") !== s; });
    $("an-students-box").hidden = !$("an-students").checked;
  }

  function fillOptions() {
    const t = data.targets;
    $("an-issuer").innerHTML = "";
    $("an-issuer").appendChild(document.createTextNode("Issued by "));
    $("an-issuer").appendChild(el("strong", null, data.issued_as.name));
    $("an-issuer").appendChild(document.createTextNode(", " + data.issued_as.designation));
    $("an-category").innerHTML = "";
    data.categories.forEach(function (c) { const o = el("option", null, c.label); o.value = c.value; $("an-category").appendChild(o); });
    $("an-programs").innerHTML = ""; $("an-semesters").innerHTML = ""; $("an-sections").innerHTML = "";
    t.programs.forEach(function (p) { chip($("an-programs"), p.id, p.code ? p.code + " — " + p.name : p.name, "prog"); });
    t.semesters.forEach(function (n) { chip($("an-semesters"), n, "Semester " + n, "sem"); });
    t.sections.forEach(function (s) { chip($("an-sections"), s.id, s.label + (s.term ? " (" + s.term + ")" : ""), "sec"); });
    $("an-sections-hint").textContent = t.sections.length ? (t.sections_are_own ? "Students enrolled in the sections you teach that you tick" : "Students enrolled in the sections you tick")
      : "You have no course sections this term.";
  }

  function reset() {
    editing = null;
    $("an-form").reset();
    $("an-students").checked = true;
    document.querySelector('input[name="an-scope"][value="ALL"]').checked = true;
    $("an-form-title").textContent = "New announcement";
    $("an-send").textContent = "Send for approval";
    $("an-reset").hidden = true;
    $("an-files").innerHTML = "";
    $("an-error").hidden = true;
    showScope();
  }

  function edit(a) {
    editing = a;
    $("an-title").value = a.title; $("an-body").value = a.body;
    $("an-category").value = a.category; $("an-priority").value = a.priority;
    $("an-expires").value = a.expires_at ? String(a.expires_at).slice(0, 10) : "";
    $("an-students").checked = a.audience_students; $("an-teachers").checked = a.audience_teachers; $("an-staff").checked = a.audience_staff;
    const sc = a.offering_ids && a.offering_ids.length ? "SECTION" : ((a.program_ids && a.program_ids.length) || (a.semesters && a.semesters.length)) ? "PROGRAM" : "ALL";
    document.querySelector('input[name="an-scope"][value="' + sc + '"]').checked = true;
    tick("an-programs", a.program_ids); tick("an-semesters", a.semesters); tick("an-sections", a.offering_ids);
    $("an-form-title").textContent = a.review === "REJECTED" ? "Fix and send again" : "Change announcement";
    $("an-send").textContent = a.review === "REJECTED" ? "Send again for approval" : "Save changes";
    $("an-reset").hidden = false;
    $("an-files").innerHTML = "";
    a.attachments.forEach(function (f) {
      const li = el("li", null, "📎 " + f.original_name);
      const rm = el("button", "link-btn", "Remove"); rm.type = "button";
      rm.addEventListener("click", async function () {
        const r = await VA.fetchJson(API + "/attachments/" + encodeURIComponent(f.id), { method: "DELETE" });
        if (r.ok) li.remove(); else VA.toast(err(r, "Could not remove."), "error");
      });
      li.appendChild(rm);
      $("an-files").appendChild(li);
    });
    showScope();
    $("an-title").focus();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function card(a) {
    const c = el("article", "an-card");
    c.setAttribute("data-review", a.review || "");
    const head = el("div", "an-card-head");
    head.appendChild(el("strong", null, a.title));
    head.appendChild(el("span", "status-pill " + (PILL[a.review] || ""), a.review_text));
    c.appendChild(head);
    c.appendChild(el("p", "an-meta", "For: " + (a.audience_text || "—") + " · sent " + day(a.submitted_at) + (a.published_at ? " · published " + day(a.published_at) : "")));
    const body = a.body.length > 240 ? a.body.slice(0, 240) + "…" : a.body;
    c.appendChild(el("p", null, body));
    if (a.review === "REJECTED" && a.review_note) c.appendChild(el("p", "an-note", "Admin: " + a.review_note));
    if (a.attachments.length) c.appendChild(el("p", "an-meta", "📎 " + a.attachments.map(function (f) { return f.original_name; }).join(", ")));
    if (a.can_edit) {
      const act = el("div", "an-card-actions");
      const e = el("button", "role-btn small-btn", a.review === "REJECTED" ? "Fix and send again" : "Change"); e.type = "button";
      e.addEventListener("click", function () { edit(a); });
      const x = el("button", "role-btn small-btn secondary-btn", "Take back"); x.type = "button";
      x.addEventListener("click", async function () {
        x.disabled = true;
        const r = await VA.fetchJson(API + "/" + encodeURIComponent(a.id) + "/cancel", { method: "POST" });
        if (!r.ok) { x.disabled = false; VA.toast(err(r, "Could not take it back."), "error"); return; }
        if (editing && editing.id === a.id) reset();
        load();
      });
      act.appendChild(e); act.appendChild(x);
      c.appendChild(act);
    }
    return c;
  }

  async function load() {
    const r = await VA.fetchJson(API);
    if (!r.ok) { $("an-mine").textContent = err(r, "Could not load."); $("an-form").hidden = true; return; }
    const first = !data;
    data = r.body;
    if (first) { fillOptions(); reset(); }
    $("an-mine").textContent = "";
    const list = data.announcements.filter(function (a) { return a.review !== "CANCELLED"; });
    if (!list.length) $("an-mine").appendChild(el("p", "empty-state", "You have not written an announcement yet."));
    list.forEach(function (a) { $("an-mine").appendChild(card(a)); });
  }

  async function send(e) {
    e.preventDefault();
    const fail = function (m) { $("an-error").textContent = m; $("an-error").hidden = false; };
    const title = $("an-title").value.trim(), body = $("an-body").value.trim();
    if (title.length < 3) { fail("Write a title."); $("an-title").focus(); return; }
    if (!body) { fail("Write the announcement."); $("an-body").focus(); return; }
    const students = $("an-students").checked, sc = scope();
    if (!students && !$("an-teachers").checked && !$("an-staff").checked) { fail("Choose who it is for."); return; }
    if (students && sc === "SECTION" && !ticked("an-sections").length) { fail("Tick at least one course section."); return; }
    const payload = { title: title, body: body, category: $("an-category").value, priority: $("an-priority").value,
      audience_students: students, audience_teachers: $("an-teachers").checked, audience_staff: $("an-staff").checked,
      program_ids: students && sc === "PROGRAM" ? ticked("an-programs") : null,
      semesters: students && sc === "PROGRAM" ? ticked("an-semesters").map(Number) : null,
      offering_ids: students && sc === "SECTION" ? ticked("an-sections") : null,
      expires_at: $("an-expires").value ? $("an-expires").value + "T23:59:00" : null };
    $("an-send").disabled = true;
    const r = await VA.fetchJson(editing ? API + "/" + encodeURIComponent(editing.id) : API,
      { method: editing ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!r.ok) { $("an-send").disabled = false; fail(err(r, "Could not send. Please try again.")); return; }
    const file = $("an-file").files[0];
    if (file) {
      const fd = new FormData(); fd.append("file", file);
      const up = await VA.fetchJson(API + "/" + encodeURIComponent(r.body.id) + "/attachments", { method: "POST", body: fd });
      if (!up.ok) VA.toast("Sent, but the file was not attached: " + err(up, "please try again."), "error");
    }
    $("an-send").disabled = false;
    VA.toast("Sent to the admin for approval. You will be told when it is published.", "success");
    reset();
    load();
  }

  document.addEventListener("vistadmin:ready", function () {
    document.querySelectorAll('input[name="an-scope"]').forEach(function (r) { r.addEventListener("change", showScope); });
    $("an-students").addEventListener("change", showScope);
    $("an-reset").addEventListener("click", reset);
    $("an-form").addEventListener("submit", send);
    load();
  });
})();
