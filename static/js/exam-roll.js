/* The exam roll of one paper (GET/PUT /api/v1/exams/papers/{id}/roll, POST .../cases).
   Used by the exam office (datesheets page) and by teachers on duty (Exam duties page).
   Separate from class attendance: only students who may sit the paper are listed; barred students are shown apart. */
(function () {
  "use strict";
  const API = "/api/v1/exams/papers/";
  const LABEL = { PRESENT: "Present", ABSENT: "Absent", LATE: "Late" };

  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function msg(r, f) { return (r.body && r.body.error && r.body.error.message) || f; }

  function open(paperId, box, onChange) {
    const VA = window.VistAdmin;
    box.hidden = false;
    box.textContent = "";
    box.appendChild(el("p", "sd-dim", "Loading the exam roll…"));

    async function load() {
      const r = await VA.fetchJson(API + encodeURIComponent(paperId) + "/roll");
      if (!r.ok) { box.textContent = ""; box.appendChild(el("p", "dash-alert error", msg(r, "Could not load the exam roll."))); return; }
      draw(r.body);
    }

    function draw(d) {
      const p = d.paper;
      box.textContent = "";
      const head = el("div", "er-head");
      const h = el("h3", "tt-sub", "Exam roll · " + p.label + " " + p.course_title);
      head.appendChild(h);
      head.appendChild(el("p", "sd-dim", p.schedule + " · " + VA.formatDateLong(p.exam_date) + " · " + p.start_time + "–" + p.end_time +
        " · room " + p.room + (d.on_duty.length ? " · on duty: " + d.on_duty.map(function (a) { return a.name + (a.role === "IN_CHARGE" ? " (in charge)" : ""); }).join(", ") : "")));
      const close = el("button", "sd-view er-close", "Close");
      close.type = "button";
      close.addEventListener("click", function () { box.hidden = true; box.textContent = ""; });
      head.appendChild(close);
      box.appendChild(head);

      const chips = el("div", "crs-chips er-chips");
      [["Present", d.counts.PRESENT], ["Late", d.counts.LATE], ["Absent", d.counts.ABSENT], ["Not marked", d.counts.unmarked], ["Not allowed", d.barred.length]]
        .forEach(function (c) { const s = el("span", "crs-chip"); s.appendChild(el("b", null, String(c[1]))); s.appendChild(document.createTextNode(" " + c[0])); chips.appendChild(s); });
      box.appendChild(chips);
      box.appendChild(el("p", "er-note", "This roll is only for the exam. It does not change anyone's class attendance."));
      if (!d.can_mark) box.appendChild(el("p", "er-info", d.is_office ? "The roll can be marked once the datesheet is published."
        : "You can mark this roll on the exam day. For a correction later, ask the exam office."));

      const err = el("p", "dash-alert error"); err.hidden = true;
      if (d.can_mark && d.students.length) {
        const bar = el("div", "er-bar");
        const all = el("button", "role-btn secondary-btn small-btn", "Mark everyone not marked as present"); all.type = "button";
        all.addEventListener("click", function () {
          box.querySelectorAll("tr[data-sid]").forEach(function (tr) {
            if (!tr.querySelector("input:checked")) tr.querySelector('input[value="PRESENT"]').checked = true;
          });
        });
        bar.appendChild(all);
        box.appendChild(bar);
      }

      const table = el("table", "dash-table er-table");
      table.innerHTML = '<thead><tr><th>Roll no.</th><th>Student</th><th>Attendance</th><th class="crs-actions"></th></tr></thead>';
      const body = el("tbody");
      if (!d.students.length) {
        const tr = el("tr"); const td = el("td", "sd-dim", "No student may sit this paper."); td.colSpan = 4; tr.appendChild(td); body.appendChild(tr);
      }
      d.students.forEach(function (s) {
        const tr = el("tr"); tr.setAttribute("data-sid", s.student_id);
        tr.appendChild(el("td", null, s.roll_number || "—"));
        tr.appendChild(el("td", null, s.name));
        const td = el("td");
        const seg = el("div", "er-seg"); seg.setAttribute("role", "radiogroup"); seg.setAttribute("aria-label", "Attendance of " + s.name);
        ["PRESENT", "LATE", "ABSENT"].forEach(function (v) {
          const l = el("label", "er-opt er-" + v.toLowerCase());
          const i = document.createElement("input"); i.type = "radio"; i.name = "er-" + s.student_id; i.value = v;
          i.checked = s.status === v; i.disabled = !d.can_mark;
          l.appendChild(i); l.appendChild(el("span", null, LABEL[v]));
          seg.appendChild(l);
        });
        td.appendChild(seg);
        tr.appendChild(td);
        const act = el("td", "crs-actions");
        const rep = el("button", "sd-view", "Report cheating"); rep.type = "button";
        rep.addEventListener("click", async function () {
          const note = await VA.ask({ title: "Unfair means case", intro: s.name + (s.roll_number ? " (" + s.roll_number + ")" : "") + " · " + p.label,
            label: "What happened (kept on record; the exam office decides)", input: "textarea", ok: "Report case", min: 5 });
          if (!note) return;
          const r = await VA.fetchJson(API + encodeURIComponent(paperId) + "/cases", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ student_id: s.student_id, note: note }) });
          if (!r.ok) { VA.toast(msg(r, "Could not report the case."), "error"); return; }
          VA.toast("Case reported to the exam office.", "success");
          load(); if (onChange) onChange();
        });
        act.appendChild(rep);
        tr.appendChild(act);
        body.appendChild(tr);
      });
      table.appendChild(body);
      box.appendChild(table);
      box.appendChild(err);

      if (d.can_mark && d.students.length) {
        const save = el("button", "role-btn", "Save exam roll"); save.type = "button";
        save.addEventListener("click", async function () {
          const entries = [];
          box.querySelectorAll("tr[data-sid]").forEach(function (tr) {
            const c = tr.querySelector("input:checked");
            if (c) entries.push({ student_id: tr.getAttribute("data-sid"), status: c.value });
          });
          if (!entries.length) { err.textContent = "Mark at least one student."; err.hidden = false; return; }
          save.disabled = true;
          const r = await VA.fetchJson(API + encodeURIComponent(paperId) + "/roll", { method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ entries: entries }) });
          save.disabled = false;
          if (!r.ok) { err.textContent = msg(r, "Could not save the roll."); err.hidden = false; return; }
          VA.toast("Exam roll saved.", "success");
          draw(r.body); if (onChange) onChange();
        });
        const foot = el("div", "ds-foot"); foot.appendChild(save);
        box.appendChild(foot);
      }

      if (d.barred.length) {
        box.appendChild(el("h4", "er-sub", "Not allowed to sit this paper (" + d.barred.length + ")"));
        const ul = el("ul", "er-barred");
        d.barred.forEach(function (b) {
          const li = el("li");
          li.appendChild(el("strong", null, b.name + (b.roll_number ? " (" + b.roll_number + ")" : "")));
          li.appendChild(document.createTextNode(" — " + b.reasons.join("; ")));
          ul.appendChild(li);
        });
        box.appendChild(ul);
      }
      if (d.cases.length) {
        box.appendChild(el("h4", "er-sub", "Unfair means cases"));
        const ul = el("ul", "er-cases");
        d.cases.forEach(function (c) {
          const li = el("li");
          li.appendChild(el("strong", null, c.student + (c.roll_number ? " (" + c.roll_number + ")" : "")));
          li.appendChild(document.createTextNode(": " + c.note + " "));
          li.appendChild(el("span", "status-pill " + (c.status === "OPEN" ? "pending" : "active"), c.status === "OPEN" ? "Waiting for decision" : "Decided"));
          if (c.decision) li.appendChild(el("div", "sd-dim", "Decision: " + c.decision));
          ul.appendChild(li);
        });
        box.appendChild(ul);
      }
      box.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    load();
  }

  window.VistExamRoll = { open: open };
})();
