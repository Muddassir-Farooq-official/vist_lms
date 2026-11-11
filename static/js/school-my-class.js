/* Teacher -> My class (School mode): the morning roll call of my section(s), and my parent-teacher meetings. */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function day(iso) { return new Date(String(iso).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }); }

  async function rollCall() {
    const r = await VA.fetchJson("/api/v1/school/roll-call/mine");
    const box = $("mc-box"), tabs = $("mc-tabs");
    if (!r.ok) { box.textContent = "Could not load your sections."; return; }
    if (!r.body.length) {
      box.textContent = "";
      box.appendChild(el("p", "empty-state", "You are not a class teacher, and nobody asked you to cover a roll call today."));
      return;
    }
    function pick(s, btn) {
      tabs.querySelectorAll(".mc-tab").forEach(function (b) { b.classList.toggle("active", b === btn); b.setAttribute("aria-selected", String(b === btn)); });
      window.VistRoll.open(s.id, box);
    }
    tabs.textContent = "";
    r.body.forEach(function (s, i) {
      const b = el("button", "mc-tab", s.class + " — " + s.label); b.type = "button"; b.setAttribute("role", "tab");
      if (s.covering) b.appendChild(el("small", null, "covering today"));
      b.addEventListener("click", function () { pick(s, b); });
      tabs.appendChild(b);
      if (i === 0) pick(s, b);
    });
    tabs.hidden = r.body.length < 2;
  }

  async function meetings() {
    const holder = $("pm-mine");
    const r = await VA.fetchJson("/api/v1/school/ptm/mine");
    holder.textContent = "";
    if (!r.ok) { holder.appendChild(el("p", "home-empty", "Could not load your meetings.")); return; }
    if (!r.body.length) { holder.appendChild(el("p", "empty-state", "No parent-teacher meeting is set. The office sets the day; parents then book times with you.")); return; }
    r.body.forEach(function (d) {
      const box = el("div", "pm-day");
      box.appendChild(el("h4", null, day(d.day) + (d.note ? " · " + d.note : "")));
      d.slots.forEach(function (s) {
        const row = el("div", "pm-slot");
        row.appendChild(el("div", "pm-time", s.start_time + "–" + s.end_time));
        const body = el("div");
        if (!s.student) { body.appendChild(el("span", "pm-free", "Free")); row.appendChild(body); box.appendChild(row); return; }
        body.appendChild(el("strong", null, s.student + (s.roll_number ? " (" + s.roll_number + ")" : "")));
        const n = el("div", "pm-notes");
        const ta = document.createElement("textarea"); ta.className = "text-input"; ta.maxLength = 1000; ta.value = s.notes || "";
        ta.placeholder = "What was discussed (only you and the office see this)"; ta.setAttribute("aria-label", "Notes for " + s.student);
        const came = el("label", "rc-day"); const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = !!s.attended;
        came.appendChild(cb); came.appendChild(document.createTextNode(" Parent came"));
        const save = el("button", "role-btn small-btn", "Save"); save.type = "button";
        save.addEventListener("click", async function () {
          const x = await VA.fetchJson("/api/v1/school/ptm/slots/" + s.id + "/notes", { method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ notes: ta.value.trim() || null, attended: cb.checked }) });
          VA.toast(x.ok ? "Saved." : "Could not save.", x.ok ? "success" : "error");
        });
        n.appendChild(ta); n.appendChild(came); n.appendChild(save);
        body.appendChild(n);
        row.appendChild(body);
        box.appendChild(row);
      });
      holder.appendChild(box);
    });
  }

  document.addEventListener("vistadmin:ready", function () { rollCall(); meetings(); });
})();
