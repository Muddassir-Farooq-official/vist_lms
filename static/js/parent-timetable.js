/* Parent -> Timetable & exams (GET /api/v1/parents/me/children/{id}/timetable). */
(function () {
  "use strict";
  const P = window.ParentChild, VA = window.VistAdmin, el = P.el;
  const $ = function (id) { return document.getElementById(id); };

  P.onChange = async function (id) {
    const r = await VA.fetchJson("/api/v1/parents/me/children/" + encodeURIComponent(id) + "/timetable");
    if (!r.ok) { P.error("Could not load the timetable. Please refresh the page."); return; }
    const d = r.body;
    $("pt-view").hidden = false;
    const ex = $("pt-exams"); ex.textContent = "";
    (d.exams || []).forEach(function (s) {
      const box = el("div", "pp-sheet");
      const head = el("div", "pp-sheet-head");
      head.appendChild(el("strong", null, s.name)); head.appendChild(el("small", null, s.term_label));
      const a = el("a", "role-btn small-btn", "Admit card");
      a.href = "/api/v1/exams/children/" + encodeURIComponent(id) + "/admit-card/" + encodeURIComponent(s.id) + "/pdf"; a.target = "_blank"; a.rel = "noopener";
      head.appendChild(a);
      box.appendChild(head);
      const ul = el("ul", "pp-list");
      (s.papers || []).forEach(function (p) {
        const li = el("li", "pp-row");
        const b = el("div", "pp-row-body");
        b.appendChild(el("strong", null, p.course_code + " · " + p.course_title));
        b.appendChild(el("small", null, P.day(p.exam_date, { weekday: "short", day: "numeric", month: "short" }) + " · " + P.time(p.start_time) + "–" + P.time(p.end_time) + (p.room ? " · Room " + p.room : "")));
        li.appendChild(b); ul.appendChild(li);
      });
      box.appendChild(ul); ex.appendChild(box);
    });
    $("pt-exams-none").hidden = (d.exams || []).length > 0;

    const days = $("pt-days"); days.textContent = "";
    const by = {};
    (d.classes || []).forEach(function (c) { (by[c.date] = by[c.date] || []).push(c); });
    Object.keys(by).sort().forEach(function (day) {
      const g = el("div", "pp-day");
      g.appendChild(el("h3", null, P.day(day, { weekday: "long", day: "numeric", month: "short" })));
      const ul = el("ul", "pp-list");
      by[day].forEach(function (c) {
        const li = el("li", "pp-row");
        li.appendChild(el("span", "pp-time", P.time(c.start) + "–" + P.time(c.end)));
        const b = el("div", "pp-row-body");
        b.appendChild(el("strong", null, c.course_code + " · " + c.course_title));
        b.appendChild(el("small", null, [c.teacher, c.room ? "Room " + c.room : ""].filter(Boolean).join(" · ")));
        li.appendChild(b); ul.appendChild(li);
      });
      g.appendChild(ul); days.appendChild(g);
    });
    $("pt-none").hidden = (d.classes || []).length > 0;
  };
})();
