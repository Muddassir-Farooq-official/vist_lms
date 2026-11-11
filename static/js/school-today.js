/* Admin -> Classes & sections: the tabs, and "Roll call": every section's roll call on a day, open one to mark or
   correct it, and set a cover teacher (GET /api/v1/school/roll-call/overview, PUT .../sections/{id}/cover). */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (s) { return VA.escapeHtml(s == null ? "" : String(s)); };
  let teachers = [];

  function tabs() {
    const btns = document.querySelectorAll("#sc-tabs [data-tab]");
    btns.forEach(function (b) {
      b.addEventListener("click", function () {
        btns.forEach(function (x) { x.classList.toggle("active", x === b); x.setAttribute("aria-selected", String(x === b)); });
        document.querySelectorAll("[data-panel]").forEach(function (p) { p.hidden = p.getAttribute("data-panel") !== b.dataset.tab; });
        if (b.dataset.tab === "today") load();
        if (b.dataset.tab === "ptm" && window.VistPtm) window.VistPtm.load();
      });
    });
  }

  async function load() {
    const day = $("td-day").value;
    const r = await VA.fetchJson("/api/v1/school/roll-call/overview" + (day ? "?day=" + day : ""));
    const body = $("td-list");
    if (!r.ok) { body.innerHTML = '<tr><td colspan="7" class="sd-dim">' + esc((r.body && r.body.error && r.body.error.message) || "Could not load.") + "</td></tr>"; return; }
    const d = r.body;
    if (!$("td-day").value) $("td-day").value = String(d.day).slice(0, 10);
    $("td-sum").textContent = d.totals.students + " students · " + d.totals.absent + " absent · " + d.totals.late + " late" +
      (d.totals.not_taken ? " · roll call not taken yet in " + d.totals.not_taken + " section" + (d.totals.not_taken === 1 ? "" : "s") : " · every roll call is taken");
    const empty = d.sections.filter(function (s) { return !s.students; }).length;
    if (empty) $("td-sum").textContent += " \u00b7 " + empty + " section" + (empty === 1 ? "" : "s") + " without students not shown";
    body.innerHTML = d.sections.filter(function (s) { return s.students; }).map(function (s) {
      const taken = !s.students ? '<span class="sd-dim">No students</span>' : s.marked >= s.students ? '<span class="status-pill active">Taken</span>'
        : s.marked ? '<span class="status-pill pending">' + s.marked + " of " + s.students + "</span>" : '<span class="status-pill dropped">Not taken</span>';
      return "<tr><td><strong>" + esc(s.class) + "</strong> — " + esc(s.label) + "</td><td>" + esc(s.class_teacher || "—") +
        (s.cover ? '<small class="sc-code">covered by ' + esc(s.cover) + "</small>" : "") + '</td><td class="num">' + s.students + "</td><td>" + taken +
        '</td><td class="num">' + s.absent + '</td><td class="num">' + s.late + '</td><td class="crs-actions"><div class="dt-actions">' +
        '<button type="button" class="sd-view" data-open="' + esc(s.id) + '">Open</button>' +
        '<button type="button" class="sd-view" data-cover="' + esc(s.id) + '" data-name="' + esc(s.class + " — " + s.label) + '">Cover</button></div></td></tr>';
    }).join("") || '<tr><td colspan="7" class="sd-dim">No section has students yet. Put students in sections on the Classes tab.</td></tr>';
    body.querySelectorAll("[data-open]").forEach(function (b) {
      b.addEventListener("click", function () {
        const box = $("td-sheet");
        box.hidden = false;
        window.VistRoll.open(b.dataset.open, box, { day: $("td-day").value, onSaved: load, onClose: function () { box.hidden = true; } });
        box.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
    body.querySelectorAll("[data-cover]").forEach(function (b) {
      b.addEventListener("click", async function () {
        if (!teachers.length) { const t = await VA.fetchJson("/api/v1/school/teachers"); teachers = t.ok ? t.body : []; }
        const choice = await VA.ask({ title: "Cover teacher", intro: b.dataset.name + " · " + $("td-day").value + ". This teacher takes the roll call that day.",
          label: "Teacher", input: "select", options: [{ value: "", label: "— Nobody (the class teacher) —" }].concat(teachers.map(function (t) { return { value: t.id, label: t.name }; })),
          ok: "Save" });
        if (choice === null || choice === undefined) return;
        const r = await VA.fetchJson("/api/v1/school/roll-call/sections/" + b.dataset.cover + "/cover", { method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ day: $("td-day").value, employee_id: choice || null }) });
        if (!r.ok) { VA.toast((r.body && r.body.error && r.body.error.message) || "Could not save.", "error"); return; }
        VA.toast(choice ? "Cover teacher set; they have been told." : "Cover removed.", "success");
        load();
      });
    });
  }

  document.addEventListener("vistadmin:ready", function () {
    tabs();
    $("td-day").addEventListener("change", function () { $("td-sheet").hidden = true; load(); });
  });
})();
