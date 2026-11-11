/* The morning roll call of one section (GET/PUT /api/v1/school/roll-call/sections/{id}).
   Used by the class teacher (My class) and by the office (Classes & sections -> Today's roll call). */
(function () {
  "use strict";
  const API = "/api/v1/school/roll-call/sections/";
  const OPTS = [["PRESENT", "Present"], ["LATE", "Late"], ["ABSENT", "Absent"], ["LEAVE", "Leave"]];

  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function msg(r, f) { return (r.body && r.body.error && r.body.error.message) || f; }

  function open(sectionId, box, opts) {
    const VA = window.VistAdmin;
    opts = opts || {};
    let day = opts.day || null;

    async function load() {
      box.textContent = "";
      box.appendChild(el("p", "sd-dim", "Loading the roll call…"));
      const r = await VA.fetchJson(API + encodeURIComponent(sectionId) + (day ? "?day=" + day : ""));
      if (!r.ok) { box.textContent = ""; box.appendChild(el("p", "dash-alert error", msg(r, "Could not load the roll call."))); return; }
      draw(r.body);
    }

    function draw(d) {
      day = String(d.day).slice(0, 10);
      box.textContent = "";
      const head = el("div", "rc-head");
      head.appendChild(el("h3", "rc-title", d.section.class + " — Section " + d.section.label));
      const when = el("label", "rc-day");
      when.appendChild(document.createTextNode("Day "));
      const di = document.createElement("input"); di.type = "date"; di.className = "text-input"; di.value = day;
      di.max = new Date(Date.now() + 5 * 3600e3).toISOString().slice(0, 10);
      di.addEventListener("change", function () { if (di.value) { day = di.value; load(); } });
      when.appendChild(di);
      head.appendChild(when);
      if (opts.onClose) {
        const c = el("button", "sd-view", "Close"); c.type = "button";
        c.addEventListener("click", opts.onClose);
        head.appendChild(c);
      }
      box.appendChild(head);
      if (d.cover) box.appendChild(el("p", "rc-note", "Covered today by " + d.cover + "."));

      const chips = el("div", "crs-chips rc-chips");
      [["Present", d.counts.PRESENT], ["Late", d.counts.LATE], ["Absent", d.counts.ABSENT], ["Leave", d.counts.LEAVE], ["Not marked", d.counts.unmarked]]
        .forEach(function (c) { const s = el("span", "crs-chip"); s.appendChild(el("b", null, String(c[1]))); s.appendChild(document.createTextNode(" " + c[0])); chips.appendChild(s); });
      box.appendChild(chips);
      if (!d.can_mark) box.appendChild(el("p", "rc-info", d.you_are === "office" ? "A day that has not come yet cannot be marked."
        : "You can mark today's roll call only. Ask the office to correct an earlier day."));

      if (!d.students.length) { box.appendChild(el("p", "empty-state", "No students in this section yet.")); return; }
      if (d.can_mark) {
        const bar = el("div", "rc-bar");
        const all = el("button", "role-btn secondary-btn small-btn", "Everyone not marked is present"); all.type = "button";
        all.addEventListener("click", function () {
          box.querySelectorAll("li[data-sid]").forEach(function (li) { if (!li.querySelector("input:checked")) li.querySelector('input[value="PRESENT"]').checked = true; });
        });
        bar.appendChild(all);
        box.appendChild(bar);
      }
      const list = el("ul", "rc-list");
      d.students.forEach(function (s) {
        const li = el("li", "rc-row"); li.setAttribute("data-sid", s.student_id);
        const who = el("div", "rc-who");
        who.appendChild(el("strong", null, s.name));
        who.appendChild(el("small", null, s.roll_number || ""));
        li.appendChild(who);
        const seg = el("div", "rc-seg"); seg.setAttribute("role", "radiogroup"); seg.setAttribute("aria-label", "Attendance of " + s.name);
        OPTS.forEach(function (o) {
          const l = el("label", "rc-opt rc-" + o[0].toLowerCase());
          const i = document.createElement("input"); i.type = "radio"; i.name = "rc-" + s.student_id; i.value = o[0];
          i.checked = s.status === o[0]; i.disabled = !d.can_mark;
          l.appendChild(i); l.appendChild(el("span", null, o[1]));
          seg.appendChild(l);
        });
        li.appendChild(seg);
        const note = document.createElement("input"); note.type = "text"; note.className = "text-input rc-note-in"; note.maxLength = 200;
        note.placeholder = "Note (optional)"; note.value = s.note || ""; note.disabled = !d.can_mark; note.setAttribute("aria-label", "Note for " + s.name);
        li.appendChild(note);
        list.appendChild(li);
      });
      box.appendChild(list);
      const err = el("p", "dash-alert error"); err.hidden = true;
      box.appendChild(err);
      if (d.can_mark) {
        const save = el("button", "role-btn", "Save roll call"); save.type = "button";
        save.addEventListener("click", async function () {
          const entries = [];
          box.querySelectorAll("li[data-sid]").forEach(function (li) {
            const c = li.querySelector("input:checked");
            if (c) entries.push({ student_id: li.getAttribute("data-sid"), status: c.value, note: li.querySelector(".rc-note-in").value.trim() || null });
          });
          if (!entries.length) { err.textContent = "Mark at least one student."; err.hidden = false; return; }
          save.disabled = true;
          const r = await VA.fetchJson(API + encodeURIComponent(sectionId), { method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ day: day, entries: entries }) });
          save.disabled = false;
          if (!r.ok) { err.textContent = msg(r, "Could not save the roll call."); err.hidden = false; return; }
          const told = r.body.is_today && (r.body.counts.ABSENT + r.body.counts.LATE) ? " Parents of absent and late children are told." : "";
          VA.toast("Roll call saved." + told, "success");
          draw(r.body);
          if (opts.onSaved) opts.onSaved(r.body);
        });
        const foot = el("div", "rc-foot"); foot.appendChild(save);
        box.appendChild(foot);
      }
    }

    load();
  }

  window.VistRoll = { open: open };
})();
