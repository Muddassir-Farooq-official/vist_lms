/* Teacher -> Exam marks & report cards (School mode).
   Marks: GET/PUT /api/v1/school/exams/{exam}/marks/{offering}.  Remarks & skills: GET/PUT /exams/{exam}/remarks/{section}. */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function msg(r, f) { return (r.body && r.body.error && r.body.error.message) || f; }
  function opt(sel, value, text) { const o = el("option", null, text); o.value = value; sel.appendChild(o); }

  // ---------------------------------------------------------------- marks
  async function loadMarks() {
    const box = $("em-sheet");
    if (!$("em-exam").value || !$("em-class").value) { box.textContent = ""; return; }
    const r = await VA.fetchJson("/api/v1/school/exams/" + $("em-exam").value + "/marks/" + $("em-class").value);
    box.textContent = "";
    if (!r.ok) { box.appendChild(el("p", "dash-alert error", msg(r, "Could not load the marks."))); return; }
    const d = r.body;
    box.appendChild(el("p", "em-meta", d.class + " — " + d.section + " · " + d.subject + " · out of " + d.total_marks));
    if (d.note) box.appendChild(el("p", "rc-info", d.note));
    const list = el("ul", "rc-list");
    d.students.forEach(function (s) {
      const li = el("li", "em-row"); li.dataset.sid = s.student_id;
      const who = el("div", "rc-who"); who.appendChild(el("strong", null, s.name)); who.appendChild(el("small", null, s.roll_number || ""));
      if (s.absent_in_roll) who.appendChild(el("small", "em-flag", "Absent in the exam roll"));
      li.appendChild(who);
      const inp = document.createElement("input"); inp.type = "number"; inp.min = "0"; inp.max = String(d.total_marks); inp.step = "0.5";
      inp.className = "text-input em-marks"; inp.value = s.marks == null ? "" : s.marks; inp.disabled = !d.can_edit || s.absent;
      inp.setAttribute("aria-label", "Marks of " + s.name);
      const ab = el("label", "em-absent"); const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = !!s.absent; cb.disabled = !d.can_edit;
      cb.addEventListener("change", function () { inp.disabled = cb.checked; if (cb.checked) inp.value = ""; });
      ab.appendChild(cb); ab.appendChild(document.createTextNode(" Absent"));
      li.appendChild(inp); li.appendChild(ab);
      list.appendChild(li);
    });
    box.appendChild(list);
    const err = el("p", "dash-alert error"); err.hidden = true; box.appendChild(err);
    if (d.can_edit) {
      const save = el("button", "role-btn", "Save marks"); save.type = "button";
      save.addEventListener("click", async function () {
        err.hidden = true;
        const entries = [];
        let bad = null;
        box.querySelectorAll("li[data-sid]").forEach(function (li) {
          const absent = li.querySelector(".em-absent input").checked;
          const v = li.querySelector(".em-marks").value.trim();
          if (!absent && v === "") return;
          const n = Number(v);
          if (!absent && (isNaN(n) || n < 0 || n > d.total_marks)) bad = li.querySelector("strong").textContent;
          entries.push({ student_id: li.dataset.sid, absent: absent, marks: absent ? null : n });
        });
        if (bad) { err.textContent = "Check the marks of " + bad + " (0 to " + d.total_marks + ")."; err.hidden = false; return; }
        if (!entries.length) { err.textContent = "Enter at least one student's marks."; err.hidden = false; return; }
        save.disabled = true;
        const x = await VA.fetchJson("/api/v1/school/exams/" + $("em-exam").value + "/marks/" + $("em-class").value,
          { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ entries: entries }) });
        save.disabled = false;
        if (!x.ok) { err.textContent = msg(x, "Could not save."); err.hidden = false; return; }
        VA.toast("Marks saved.", "success");
        loadMarks();
      });
      const foot = el("div", "rc-foot"); foot.appendChild(save); box.appendChild(foot);
    }
  }

  // ---------------------------------------------------------------- remarks & skills
  async function loadNotes() {
    const box = $("en-sheet");
    if (!$("en-exam").value || !$("en-section").value) { box.textContent = ""; return; }
    const r = await VA.fetchJson("/api/v1/school/exams/" + $("en-exam").value + "/remarks/" + $("en-section").value);
    box.textContent = "";
    if (!r.ok) { box.appendChild(el("p", "dash-alert error", msg(r, "Could not load."))); return; }
    const d = r.body;
    if (!d.can_edit) box.appendChild(el("p", "rc-info", "These report cards are published. Ask the office to withdraw them to make changes."));
    box.appendChild(el("p", "em-meta", "Rate each skill: A Excellent · B Good · C Fair · D Needs improvement."));
    d.students.forEach(function (s) {
      const card = el("div", "en-card"); card.dataset.sid = s.student_id;
      const h = el("div", "rc-who"); h.appendChild(el("strong", null, s.name)); h.appendChild(el("small", null, s.roll_number || ""));
      card.appendChild(h);
      const grid = el("div", "en-skills");
      d.skills.forEach(function (k) {
        const l = el("label", null, k.name);
        const sel = document.createElement("select"); sel.className = "select-input"; sel.dataset.skill = k.id; sel.disabled = !d.can_edit;
        ["", "A", "B", "C", "D"].forEach(function (g) { opt(sel, g, g || "—"); });
        sel.value = (s.skills || {})[k.id] || "";
        l.appendChild(sel);
        grid.appendChild(l);
      });
      card.appendChild(grid);
      const ta = document.createElement("textarea"); ta.className = "text-input"; ta.maxLength = 600; ta.rows = 2; ta.value = s.remarks || ""; ta.disabled = !d.can_edit;
      ta.placeholder = "Remarks for the report card"; ta.setAttribute("aria-label", "Remarks for " + s.name);
      card.appendChild(ta);
      box.appendChild(card);
    });
    if (d.can_edit && d.students.length) {
      const save = el("button", "role-btn", "Save remarks & skills"); save.type = "button";
      save.addEventListener("click", async function () {
        const entries = [];
        box.querySelectorAll(".en-card").forEach(function (c) {
          const skills = {};
          c.querySelectorAll("select[data-skill]").forEach(function (s) { if (s.value) skills[s.dataset.skill] = s.value; });
          entries.push({ student_id: c.dataset.sid, remarks: c.querySelector("textarea").value.trim() || null, skills: skills });
        });
        save.disabled = true;
        const x = await VA.fetchJson("/api/v1/school/exams/" + $("en-exam").value + "/remarks/" + $("en-section").value,
          { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ entries: entries }) });
        save.disabled = false;
        VA.toast(x.ok ? "Saved." : msg(x, "Could not save."), x.ok ? "success" : "error");
      });
      const foot = el("div", "rc-foot"); foot.appendChild(save); box.appendChild(foot);
    }
  }

  document.addEventListener("vistadmin:ready", async function () {
    const r = await VA.fetchJson("/api/v1/school/marks/mine");
    if (!r.ok || !r.body.exams.length) { $("em-none").hidden = false; return; }
    const d = r.body;
    $("em-view").hidden = false;
    d.exams.forEach(function (e) { opt($("em-exam"), e.id, e.name + (e.marks_open ? "" : " (closed)")); opt($("en-exam"), e.id, e.name); });
    d.classes.forEach(function (c) { opt($("em-class"), c.offering_id, c.class + " — " + c.section + " · " + c.subject); });
    if (!d.classes.length) $("em-sheet").appendChild(el("p", "empty-state", "You do not teach a subject this session."));
    d.class_teacher_of.forEach(function (c) { opt($("en-section"), c.section_id, c.class + " — " + c.section); });
    $("em-notes-tab").hidden = !d.class_teacher_of.length;
    document.querySelectorAll("[data-pane]").forEach(function (b) {
      b.addEventListener("click", function () {
        document.querySelectorAll("[data-pane]").forEach(function (x) { x.classList.toggle("active", x === b); x.setAttribute("aria-selected", String(x === b)); });
        document.querySelectorAll("[data-pane-box]").forEach(function (p) { p.hidden = p.getAttribute("data-pane-box") !== b.dataset.pane; });
        if (b.dataset.pane === "notes") loadNotes();
      });
    });
    $("em-exam").addEventListener("change", loadMarks); $("em-class").addEventListener("change", loadMarks);
    $("en-exam").addEventListener("change", loadNotes); $("en-section").addEventListener("change", loadNotes);
    loadMarks();
  });
})();
