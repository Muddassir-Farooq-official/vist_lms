/* Admin -> Classes & sections (School mode). API: /api/v1/school/... */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const API = "/api/v1/school";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (s) { return VA.escapeHtml(s == null ? "" : String(s)); };
  let data = null, teachers = [], current = null;    // current = the open section's detail

  function msg(r, f) { return (r.body && r.body.error && r.body.error.message) || f; }
  function json(method, body) { return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }; }
  function sessionLabel() { return $("sc-session").value || (data && data.session && data.session.label) || ""; }

  // ------------------------------------------------------------------ loading
  async function load(label) {
    const r = await VA.fetchJson(API + "/overview" + (label ? "?session_label=" + encodeURIComponent(label) : ""));
    if (!r.ok) { $("sc-classes").innerHTML = '<p class="dash-alert error">' + esc(msg(r, "Could not load the classes.")) + "</p>"; return; }
    data = r.body;
    $("sc-off").hidden = data.school_mode;
    if (!data.school_mode && !data.classes.length) { $("sc-classes").innerHTML = ""; return; }
    fillSessions();
    fillGroups();
    if (!data.classes.length) {
      $("sc-bar").hidden = !data.session;
      $("sc-classes").innerHTML = "";
      openSetup();
      return;
    }
    $("sc-bar").hidden = false;
    drawClasses();
  }

  function fillSessions() {
    const sel = $("sc-session");
    sel.innerHTML = "";
    data.sessions.forEach(function (t) {
      const o = document.createElement("option");
      o.value = t.label; o.textContent = t.label + (t.status === "CLOSED" ? " (closed)" : "");
      sel.appendChild(o);
    });
    if (data.session) sel.value = data.session.label;
  }

  // ------------------------------------------------------------------ set-up form
  function fillGroups() {
    if ($("sc-groups").childElementCount) return;
    const byStage = {};
    data.catalogue.forEach(function (g) { (byStage[g.stage_label] = byStage[g.stage_label] || []).push(g); });
    $("sc-groups").innerHTML = Object.keys(byStage).map(function (stage) {
      return '<fieldset class="sc-stage"><legend>' + esc(stage) + "</legend>" + byStage[stage].map(function (g) {
        const tip = g.classes.map(function (c) { return c.name + ": " + c.subjects.join(", "); }).join("\n");
        return '<label class="sc-group" title="' + esc(tip) + '"><input type="checkbox" value="' + esc(g.key) + '"> <span>' + esc(g.label) +
          '</span><small>' + esc(g.classes[0].subjects.slice(0, 5).join(", ") + (g.classes[0].subjects.length > 5 ? "…" : "")) + "</small></label>";
      }).join("") + "</fieldset>";
    }).join("");
  }

  function openSetup() {
    $("sc-setup").hidden = false;
    if (data.session && !$("sc-term").value) {
      $("sc-term").value = data.session.label;
      $("sc-start").value = data.session.start_date || "";
      $("sc-end").value = data.session.end_date || "";
    }
    $("sc-setup-cancel").hidden = !data.classes.length;
    $("sc-setup").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function setup(e) {
    e.preventDefault();
    const err = $("sc-setup-error"); err.hidden = true;
    const groups = Array.prototype.map.call(document.querySelectorAll("#sc-groups input:checked"), function (c) { return c.value; });
    const term = $("sc-term").value.trim();
    if (!term) { err.textContent = "Name the session, e.g. 2026-27."; err.hidden = false; $("sc-term").focus(); return; }
    if (!groups.length) { err.textContent = "Tick at least one group of classes."; err.hidden = false; return; }
    const sections = $("sc-sections").value.split(/[,\s]+/).map(function (s) { return s.trim(); }).filter(Boolean);
    $("sc-setup-go").disabled = true;
    const r = await VA.fetchJson(API + "/setup", json("POST", { term_label: term, start_date: $("sc-start").value || null, end_date: $("sc-end").value || null,
      groups: groups, sections: sections.length ? sections : ["A"] }));
    $("sc-setup-go").disabled = false;
    if (!r.ok) { err.textContent = msg(r, "Could not set up the classes."); err.hidden = false; return; }
    const c = r.body.created;
    VA.toast("Done: " + c.classes + " new classes, " + c.sections + " sections, " + c.subjects + " subjects." +
      (r.body.skipped.length ? " Skipped (a program with that code already exists): " + r.body.skipped.join(", ") + "." : ""), "success");
    $("sc-setup").hidden = true;
    document.querySelectorAll("#sc-groups input").forEach(function (x) { x.checked = false; });
    load(r.body.session);
  }

  // ------------------------------------------------------------------ the classes
  function drawClasses() {
    const stages = [];
    data.classes.forEach(function (c) {
      let st = stages.find(function (s) { return s.key === c.stage; });
      if (!st) { st = { key: c.stage, label: c.stage_label, items: [] }; stages.push(st); }
      st.items.push(c);
    });
    $("sc-classes").innerHTML = stages.map(function (st) {
      return '<section class="sc-stage-block"><h2 class="sc-stage-h">' + esc(st.label) + '</h2><div class="sc-grid">' + st.items.map(card).join("") + "</div></section>";
    }).join("");
    document.querySelectorAll("[data-section]").forEach(function (b) { b.addEventListener("click", function () { openSection(b.dataset.section); }); });
    document.querySelectorAll("[data-add-section]").forEach(function (b) {
      b.addEventListener("click", async function () {
        const label = await VA.ask({ title: "Add a section", intro: b.dataset.name + " · " + sessionLabel(), label: "Section name, e.g. B", input: "text", ok: "Add section", min: 1 });
        if (!label) return;
        const r = await VA.fetchJson(API + "/classes/" + b.dataset.addSection + "/sections", json("POST", { label: label, term_label: sessionLabel() }));
        if (!r.ok) { VA.toast(msg(r, "Could not add the section."), "error"); return; }
        VA.toast("Section " + r.body.label + " added.", "success");
        await load(sessionLabel());
        openSection(r.body.id);
      });
    });
  }

  function card(c) {
    const pupils = c.sections.reduce(function (n, s) { return n + s.students; }, 0);
    return '<article class="sc-card"><header><h3>' + esc(c.name) + "</h3><span class=\"sc-meta\">" + esc(c.subjects.length + " subjects · " + pupils + " students" +
        (c.next_class ? " · next: " + c.next_class : "")) + "</span></header>" +
      (c.without_section ? '<p class="sc-warn">' + c.without_section + " student" + (c.without_section === 1 ? " is" : "s are") + " in this class without a section.</p>" : "") +
      '<div class="sc-sections">' + c.sections.map(function (s) {
        return '<button type="button" class="sc-sec" data-section="' + esc(s.id) + '"><b>' + esc(s.label) + "</b><span>" + s.students + (s.capacity ? " / " + s.capacity : "") +
          " students</span><small>" + esc(s.class_teacher || "No class teacher yet") + "</small></button>";
      }).join("") + '<button type="button" class="sc-sec sc-add" data-add-section="' + esc(c.id) + '" data-name="' + esc(c.name) + '">+ Section</button></div></article>';
  }

  // ------------------------------------------------------------------ one section
  async function openSection(id) {
    const r = await VA.fetchJson(API + "/sections/" + id);
    if (!r.ok) { VA.toast(msg(r, "Could not open the section."), "error"); return; }
    current = r.body;
    drawSection();
    $("sc-detail").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function teacherOptions(selected) {
    return '<option value="">— Not set —</option>' + teachers.map(function (t) {
      return '<option value="' + esc(t.id) + '"' + (t.id === selected ? " selected" : "") + ">" + esc(t.name + (t.employee_code ? " (" + t.employee_code + ")" : "")) + "</option>";
    }).join("");
  }

  function drawSection() {
    const d = current, box = $("sc-detail");
    const optional = d.subjects.filter(function (s) { return s.optional; });
    box.hidden = false;
    box.innerHTML =
      '<div class="sc-detail-head"><h2 class="panel-title">' + esc(d.class.name + " — Section " + d.label) + '</h2><span class="sc-meta">' + esc(d.session) + "</span>" +
      '<button type="button" class="sd-view" id="sc-close">Close</button></div>' +
      '<div class="sc-row"><label>Class teacher<select id="sc-ct" class="select-input">' + teacherOptions(d.class_teacher_id) + "</select></label>" +
      '<label>Room<input id="sc-room" class="text-input" maxlength="50" value="' + esc(d.room || "") + '"></label>' +
      '<label>Seats<input id="sc-cap" class="text-input" type="number" min="1" value="' + esc(d.capacity || "") + '" placeholder="No limit"></label>' +
      '<button type="button" class="role-btn" id="sc-save-sec">Save</button></div>' +

      '<h3 class="sc-sub">Subjects and teachers</h3><table class="dash-table"><thead><tr><th>Subject</th><th>Teacher</th><th class="num">Students</th><th class="crs-actions"></th></tr></thead><tbody>' +
      d.subjects.map(function (s) {
        return "<tr><td>" + esc(s.title) + (s.optional ? ' <span class="sc-tag">choice</span>' : "") + '<small class="sc-code">' + esc(s.code) + "</small></td>" +
          '<td><select class="select-input" data-teacher="' + esc(s.course_id) + '" aria-label="Teacher for ' + esc(s.title) + '">' + teacherOptions(s.teacher_id) + "</select></td>" +
          '<td class="num">' + s.students + '</td><td class="crs-actions"><button type="button" class="sd-view tt-del" data-drop-subject="' + esc(s.course_id) + '" data-title="' + esc(s.title) + '">Remove</button></td></tr>';
      }).join("") + "</tbody></table>" +
      '<div class="sc-row"><button type="button" class="role-btn" id="sc-save-teachers">Save teachers</button>' +
      '<form id="sc-add-subject" class="sc-inline" novalidate><input id="sc-sub-title" class="text-input" maxlength="120" placeholder="New subject, e.g. Arabic" aria-label="New subject">' +
      '<input id="sc-sub-code" class="text-input sc-short" maxlength="8" placeholder="Code" aria-label="Short code"><label class="sc-check"><input type="checkbox" id="sc-sub-opt"> Choice</label>' +
      '<button type="submit" class="role-btn secondary-btn">Add subject</button></form></div>' +

      '<h3 class="sc-sub">Students (' + d.students.length + (d.capacity ? " of " + d.capacity + " seats" : "") + ")</h3>" +
      (d.students.length ? '<table class="dash-table"><thead><tr><th>Roll no.</th><th>Name</th><th class="num">Subjects</th></tr></thead><tbody>' + d.students.map(function (p) {
        return "<tr><td>" + esc(p.roll_number) + "</td><td>" + esc(p.name) + '</td><td class="num">' + p.subjects.length + "</td></tr>";
      }).join("") + "</tbody></table>" : '<p class="empty-state">No students in this section yet.</p>') +

      '<div class="sc-place"><h3 class="sc-sub">Add students to this section</h3>' +
      '<input id="sc-find" class="text-input" type="search" placeholder="Search by name or roll number" aria-label="Find students">' +
      '<ul id="sc-cands" class="sc-cands"></ul>' +
      (optional.length ? '<fieldset class="sc-opt"><legend>Their choice subjects</legend>' + optional.map(function (s) {
        return '<label><input type="checkbox" value="' + esc(s.course_id) + '"> ' + esc(s.title) + "</label>";
      }).join("") + "</fieldset>" : "") +
      '<p class="form-error" id="sc-place-error" hidden></p><button type="button" class="role-btn" id="sc-place-go">Put the ticked students in Section ' + esc(d.label) + "</button>" +
      '<p class="sc-hint">They are enrolled in every subject of the class' + (optional.length ? " and the choice subjects ticked" : "") +
      ". A student moving from another section takes their subjects along (before classes start).</p></div>" +
      (d.students.length ? "" : '<div class="sc-row"><button type="button" class="role-btn secondary-btn tt-del" id="sc-remove-sec">Remove this section</button></div>');

    $("sc-close").addEventListener("click", function () { box.hidden = true; current = null; });
    $("sc-save-sec").addEventListener("click", saveSection);
    $("sc-save-teachers").addEventListener("click", saveTeachers);
    $("sc-add-subject").addEventListener("submit", addSubject);
    $("sc-place-go").addEventListener("click", place);
    let t = null;
    $("sc-find").addEventListener("input", function () { clearTimeout(t); t = setTimeout(findStudents, 250); });
    box.querySelectorAll("[data-drop-subject]").forEach(function (b) {
      b.addEventListener("click", async function () {
        if (!(await VA.confirm("Take " + b.dataset.title + " off " + d.class.name + "? This works only while nobody is enrolled in it.", "Remove subject"))) return;
        const r = await VA.fetchJson(API + "/classes/" + d.class.id + "/subjects/" + b.dataset.dropSubject + "?session_label=" + encodeURIComponent(d.session), { method: "DELETE" });
        if (!r.ok) { VA.toast(msg(r, "Could not remove the subject."), "error"); return; }
        VA.toast(b.dataset.title + " removed.", "success");
        refresh();
      });
    });
    const rm = $("sc-remove-sec");
    if (rm) rm.addEventListener("click", async function () {
      if (!(await VA.confirm("Remove Section " + d.label + " of " + d.class.name + "?", "Remove section"))) return;
      const r = await VA.fetchJson(API + "/sections/" + d.id, { method: "DELETE" });
      if (!r.ok) { VA.toast(msg(r, "Could not remove the section."), "error"); return; }
      box.hidden = true; current = null;
      load(sessionLabel());
    });
    findStudents();
  }

  async function refresh() {
    await load(sessionLabel());
    if (current) openSection(current.id);
  }

  async function saveSection() {
    const cap = $("sc-cap").value.trim();
    const r = await VA.fetchJson(API + "/sections/" + current.id, json("PATCH", { class_teacher_id: $("sc-ct").value || null, room: $("sc-room").value.trim() || null,
      capacity: cap ? Number(cap) : null }));
    if (!r.ok) { VA.toast(msg(r, "Could not save."), "error"); return; }
    VA.toast("Saved.", "success");
    current = r.body; drawSection(); load(sessionLabel());
  }

  async function saveTeachers() {
    const map = {};
    document.querySelectorAll("#sc-detail [data-teacher]").forEach(function (s) { map[s.dataset.teacher] = s.value || null; });
    const r = await VA.fetchJson(API + "/sections/" + current.id + "/teachers", json("PUT", { teachers: map }));
    if (!r.ok) { VA.toast(msg(r, "Could not save the teachers."), "error"); return; }
    VA.toast("Teachers saved. Each teacher is told about their new class.", "success");
    current = r.body; drawSection();
  }

  async function addSubject(e) {
    e.preventDefault();
    const title = $("sc-sub-title").value.trim();
    if (title.length < 2) { $("sc-sub-title").focus(); return; }
    const r = await VA.fetchJson(API + "/classes/" + current.class.id + "/subjects", json("POST", { title: title, code: $("sc-sub-code").value.trim() || null,
      optional: $("sc-sub-opt").checked, term_label: current.session }));
    if (!r.ok) { VA.toast(msg(r, "Could not add the subject."), "error"); return; }
    VA.toast(title + " added to " + current.class.name + " (every section).", "success");
    refresh();
  }

  async function findStudents() {
    const q = $("sc-find") ? $("sc-find").value.trim() : "";
    const r = await VA.fetchJson(API + "/sections/" + current.id + "/candidates" + (q ? "?q=" + encodeURIComponent(q) : ""));
    const ul = $("sc-cands");
    if (!ul) return;
    if (!r.ok) { ul.innerHTML = '<li class="sc-dim">' + esc(msg(r, "Could not search.")) + "</li>"; return; }
    ul.innerHTML = r.body.length ? r.body.map(function (s) {
      const where = s.class ? s.class + (s.section ? " · Section " + s.section : " · no section") : "No class yet";
      return '<li><label><input type="checkbox" value="' + esc(s.id) + '"> <b>' + esc(s.name) + "</b> <span>" + esc(s.roll_number) + '</span> <small>' + esc(where) + "</small></label></li>";
    }).join("") : '<li class="sc-dim">' + (q ? "Nobody found." : "Everyone in this class already has a section. Search to find other students.") + "</li>";
  }

  async function place() {
    const err = $("sc-place-error"); err.hidden = true;
    const ids = Array.prototype.map.call(document.querySelectorAll("#sc-cands input:checked"), function (c) { return c.value; });
    if (!ids.length) { err.textContent = "Tick the students to add."; err.hidden = false; return; }
    const opt = Array.prototype.map.call(document.querySelectorAll(".sc-opt input:checked"), function (c) { return c.value; });
    $("sc-place-go").disabled = true;
    const r = await VA.fetchJson(API + "/sections/" + current.id + "/students", json("POST", { student_ids: ids, optional_course_ids: opt }));
    $("sc-place-go").disabled = false;
    if (!r.ok) { err.textContent = msg(r, "Could not add them."); err.hidden = false; return; }
    current = r.body.section;
    drawSection();
    if (r.body.placed.length) VA.toast(r.body.placed.length + " student" + (r.body.placed.length === 1 ? "" : "s") + " added to Section " + current.label + ".", "success");
    if (r.body.failed.length) {
      const e2 = $("sc-place-error");
      e2.textContent = "Not added: " + r.body.failed.map(function (f) { return (f.name || "a student") + " (" + f.reason + ")"; }).join("; ");
      e2.hidden = false;
    }
    load(sessionLabel());
  }

  document.addEventListener("vistadmin:ready", async function () {
    $("sc-session").addEventListener("change", function () { $("sc-detail").hidden = true; current = null; load($("sc-session").value); });
    $("sc-open-setup").addEventListener("click", openSetup);
    $("sc-setup-cancel").addEventListener("click", function () { $("sc-setup").hidden = true; });
    $("sc-setup-form").addEventListener("submit", setup);
    const t = await VA.fetchJson(API + "/teachers");
    teachers = t.ok ? t.body : [];
    load();
  });
})();
