/* Faculty -> Students to watch (GET /api/v1/academics/offerings/me/watchlist). */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  let data = null, course = "";

  function initials(n) { return (n || "?").split(/\s+/).filter(Boolean).slice(0, 2).map(function (w) { return w[0]; }).join("").toUpperCase(); }
  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }

  // One card per student: course-specific reasons carry the course code; the fee reason is shown once.
  function grouped(rows) {
    const by = {}, order = [];
    rows.forEach(function (s) {
      let g = by[s.student_id];
      if (!g) { g = by[s.student_id] = { name: s.name, roll: s.roll_number, courses: [], reasons: [], seen: {}, weight: 0 }; order.push(g); }
      g.courses.push(s.course_code);
      s.reasons.forEach(function (r) {
        const shared = r.kind === "fees";
        const key = shared ? "fees" : s.course_code + r.text;
        if (g.seen[key]) return;
        g.seen[key] = true;
        g.reasons.push({ kind: r.kind, text: shared || course ? r.text : s.course_code + ": " + r.text });
        if (!shared) g.weight += 1;
      });
    });
    order.forEach(function (g) { g.reasons.sort(function (a, b) { return (a.kind === "fees") - (b.kind === "fees"); }); });
    return order.sort(function (a, b) { return b.weight - a.weight || b.reasons.length - a.reasons.length || a.name.localeCompare(b.name); });
  }

  function draw() {
    const list = $("wl-list");
    list.textContent = "";
    const rows = grouped(data.students.filter(function (s) { return !course || s.offering_id === course; }));
    rows.forEach(function (g) {
      const li = el("li", "wl-item" + (g.weight > 1 ? " many" : ""));
      li.appendChild(el("span", "wl-avatar", initials(g.name)));
      const body = el("div");
      const top = el("div", "wl-top");
      const who = el("span");
      who.appendChild(el("strong", null, g.name));
      who.appendChild(document.createTextNode(" "));
      who.appendChild(el("small", null, g.roll));
      top.appendChild(who);
      const cs = el("span");
      g.courses.forEach(function (c) { cs.appendChild(el("span", "wl-course", c)); cs.appendChild(document.createTextNode(" ")); });
      top.appendChild(cs);
      body.appendChild(top);
      const ul = el("ul", "wl-reasons");
      g.reasons.forEach(function (r) { ul.appendChild(el("li", "r-" + r.kind, r.text)); });
      body.appendChild(ul);
      li.appendChild(body);
      list.appendChild(li);
    });
    $("wl-empty").hidden = rows.length > 0;
    list.hidden = rows.length === 0;
    const ids = {};
    data.courses.filter(function (c) { return !course || c.offering_id === course; }).forEach(function (c) { ids[c.offering_id] = c.enrolled; });
    const total = course ? ids[course] : data.total_students;
    $("wl-summary").textContent = rows.length ? rows.length + " of " + total + " students need attention" + (course ? " in this course." : " across your courses.") : "";
  }

  async function load() {
    const r = await VA.fetchJson("/api/v1/academics/offerings/me/watchlist");
    if (!r.ok) { $("wl-list").innerHTML = ""; $("wl-summary").textContent = (r.body && r.body.error && r.body.error.message) || "Could not load the list."; return; }
    data = r.body;
    const chips = $("wl-courses");
    const mk = function (label, id, count) {
      const b = el("button", id === course ? "active" : "", label); b.type = "button"; b.dataset.id = id;
      if (count) b.appendChild(el("b", null, String(count)));
      chips.appendChild(b);
    };
    if (data.courses.length > 1) {
      mk("All courses", "", new Set(data.students.map(function (x) { return x.student_id; })).size);
      data.courses.forEach(function (c) { mk(c.course_code + (c.section_label && c.section_label !== "A" ? " · " + c.section_label : ""), c.offering_id, c.count); });
      chips.addEventListener("click", function (e) {
        const b = e.target.closest("button"); if (!b) return;
        course = b.dataset.id;
        Array.prototype.forEach.call(chips.children, function (x) { x.classList.toggle("active", x === b); });
        draw();
      });
    }
    const rules = $("wl-rules");
    data.rules.forEach(function (t) { rules.appendChild(el("li", null, t)); });
    if (!data.courses.length) { $("wl-summary").textContent = "You have no courses running this term."; }
    draw();
  }

  document.addEventListener("vistadmin:ready", load);
})();
